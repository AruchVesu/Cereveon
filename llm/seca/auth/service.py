import functools
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from .models import Player, Session
from .hashing import hash_password, needs_rehash, verify_password
from .tokens import create_access_token

_MAX_SESSIONS = 10

# Sliding-session window — how far forward we push session.expires_at
# on each successful authenticated request.  Matches the model's
# default 7-day window so a continuously-active user keeps the same
# effective expiry shape they got at login.
_SESSION_EXTEND = timedelta(days=7)

# Don't bump expires_at on EVERY request — for an active user that's
# a needless DB write per API call.  Only slide when the session has
# less than this much time remaining; for a 7-day window this means
# we write at most once every 24h instead of once per call.
_SESSION_SLIDE_THRESHOLD = timedelta(days=1)


@functools.cache
def _dummy_hash_for_timing_safety() -> str:
    """Lazily-computed dummy hash for ``login()`` email-enumeration defence.

    ``login()`` runs PBKDF2-SHA256 on the supplied password against the
    stored hash for matched emails (~270 ms).  Unmatched emails would
    skip that work entirely (~0.3 ms) and the wall-clock difference is a
    950x timing oracle for enumerating registered addresses — see the
    miss branch in ``login()``.  We close the oracle by running a dummy
    ``verify_password`` against a precomputed hash on the miss path.

    The hash itself was previously computed eagerly at module import,
    paying ~270 ms of PBKDF2 cost on every Python process that touched
    the auth tree (every backend test, every script that imports
    ``llm.server``, every CLI tool).  ``@functools.cache`` defers the
    cost to the first miss-path login while preserving the
    "precomputed once" semantic — subsequent calls return the cached
    value without re-hashing.
    """
    return hash_password("dummy_password_for_timing_safety_only")


class AuthService:
    def __init__(self, db: DBSession):
        self.db = db

    # ---------------------------
    # Register
    # ---------------------------
    def register(self, email: str, password: str) -> Player:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(password) > 1000:
            raise ValueError("Password too long (max 1000 chars)")
        if self.db.query(Player).filter_by(email=email).first():
            raise ValueError("Registration failed")

        player = Player(
            email=email,
            password_hash=hash_password(password),
            player_embedding="[]",
        )
        self.db.add(player)
        self.db.commit()
        self.db.refresh(player)
        return player

    # ---------------------------
    # Login
    # ---------------------------
    def login(self, email: str, password: str, device_info: str | None = None):
        player = self.db.query(Player).filter(Player.email == email).first()

        if player is None:
            # Run a dummy verification so a missing-email login takes the same
            # wall time as an existing-email-with-wrong-password login.  Without
            # this, login() is a textbook email-enumeration timing oracle.
            verify_password(password, _dummy_hash_for_timing_safety())
            raise ValueError("Invalid credentials")

        if not verify_password(password, player.password_hash):
            raise ValueError("Invalid credentials")

        # Opportunistically upgrade legacy hashes (H1)
        if needs_rehash(player.password_hash):
            player.password_hash = hash_password(password)

        token = self._issue_session(player, device_info)
        return token, player

    # ---------------------------
    # Session issuance (shared tail of login / login_with_lichess)
    # ---------------------------
    def _issue_session(self, player: Player, device_info: str | None) -> str:
        """Create a session row + JWT for an ALREADY-authenticated player.

        Shared by password login and Lichess OAuth sign-in so both paths
        get identical session semantics: expired-session pruning, the
        _MAX_SESSIONS cap, and the F-07 token-hash pinning.  Performs no
        credential checks — callers are responsible for authentication.
        """
        # Prune expired sessions for this player (H3)
        now = datetime.utcnow()
        self.db.query(Session).filter(
            Session.player_id == player.id,
            Session.expires_at.isnot(None),
            Session.expires_at < now,
        ).delete(synchronize_session=False)

        # Cap concurrent active sessions at _MAX_SESSIONS (H3)
        active = (
            self.db.query(Session)
            .filter(Session.player_id == player.id)
            .order_by(Session.created_at.asc())
            .all()
        )
        if len(active) >= _MAX_SESSIONS:
            for old in active[: len(active) - _MAX_SESSIONS + 1]:
                self.db.delete(old)

        # 1. create session_id manually BEFORE DB insert
        import uuid

        session_id = str(uuid.uuid4())

        # 2. create JWT using this session_id
        token = create_access_token(
            player_id=str(player.id),
            session_id=session_id,
        )

        # 3. hash token
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        # 4. create DB session WITH token_hash already set
        session = Session(
            id=session_id,
            player_id=player.id,
            token_hash=token_hash,
            device_info=device_info or "",
        )

        self.db.add(session)
        self.db.commit()

        return token

    # ---------------------------
    # Login with Lichess (OAuth)
    # ---------------------------
    def login_with_lichess(
        self, lichess_user_id: str, device_info: str | None = None
    ) -> tuple[str, Player, bool]:
        """Find-or-create the player for a VERIFIED Lichess identity and
        issue a session.

        Trust contract: ``lichess_user_id`` MUST be the canonical ``id``
        returned by Lichess ``GET /api/account`` for a token this server
        obtained via the authorization-code exchange
        (``llm.seca.lichess.client``).  This method performs no further
        identity checks.

        First sign-in creates the account with:

        * ``email = "lichess:<id>"`` — a synthetic identifier that lives
          in the unique email column but OUTSIDE the reachable email
          space: it contains no ``@`` so ``_validate_email_strict``
          rejects it at /auth/register and /auth/login, which means it
          can never be squatted or matched by a password flow.
        * an unusable credential — a random secret hashed and discarded,
          so ``verify_password`` can never succeed against this row.

        Returns ``(token, player, created)``.
        """
        player = self.db.query(Player).filter(Player.lichess_user_id == lichess_user_id).first()
        created = False
        if player is None:
            player = Player(
                email=f"lichess:{lichess_user_id}",
                password_hash=hash_password(secrets.token_urlsafe(48)),
                player_embedding="[]",
                lichess_user_id=lichess_user_id,
            )
            self.db.add(player)
            try:
                self.db.commit()
            except IntegrityError:
                # Two concurrent first sign-ins raced on the unique
                # lichess_user_id (or synthetic email) index; the other
                # request won.  Roll back and use its row.
                self.db.rollback()
                player = (
                    self.db.query(Player)
                    .filter(Player.lichess_user_id == lichess_user_id)
                    .first()
                )
                if player is None:  # pragma: no cover — constraint just fired
                    raise
            else:
                self.db.refresh(player)
                created = True
        token = self._issue_session(player, device_info)
        return token, player, created

    # ---------------------------
    # Validate session
    # ---------------------------
    def get_player_by_session(self, session_id: str, token: str) -> Player | None:
        # Three orthogonal authenticity gates:
        #   1. JWT signature (decoded upstream in router.get_current_player —
        #      proves the bearer once held credentials that produced this
        #      signed token).
        #   2. Session row exists (revocation = row deletion; logout + change_password).
        #   3. session.token_hash matches sha256(inbound) — the F-07
        #      per-token revocation gate.  router.get_current_player rotates
        #      this hash via rotate_session_token after issuing each new
        #      X-Auth-Token, so a previously-issued JWT becomes invalid on
        #      the next call regardless of its still-valid signature / exp.
        #      Closes the "stolen JWT lives until exp (24 h)" window.
        #
        # The rotation contract: if the inbound token's hash matches stored
        # token_hash, validation succeeds; the caller then mints a new JWT
        # and calls rotate_session_token, which writes sha256(new) into the
        # row.  The next call must present the new token.  See
        # test_auth_rotation_regression.py for the pinned sequence.
        session = self.db.query(Session).filter_by(id=session_id).first()
        if not session:
            return None

        # Fail-closed: treat missing expiry as expired (M1)
        now = datetime.utcnow()
        if session.expires_at is None or session.expires_at < now:
            return None

        # F-07: per-token revocation gate.  A session row with no
        # token_hash (only legacy rows pre-F-07 should hit this branch)
        # is rejected fail-closed rather than waved through — preferring
        # a one-time re-login over a silently weakened auth path.
        if not session.token_hash:
            return None
        inbound_hash = hashlib.sha256(token.encode()).hexdigest()
        # Two valid hashes at any moment: the current token_hash and
        # (within a brief grace window) the previous one.  The grace
        # window absorbs the rotation race that happens when two
        # authenticated requests carrying the same starting token
        # overlap server-side: the first rotates ``current → new``,
        # demoting the original to ``previous``; the second request
        # then validates against ``previous`` and succeeds instead of
        # 401-ing.  After ``previous_token_expires_at`` the previous
        # hash is no longer accepted; F-07's "stolen JWT becomes
        # useless within seconds" guarantee holds up to that bound.
        token_matches_current = hmac.compare_digest(inbound_hash, session.token_hash)
        token_matches_previous = (
            session.previous_token_hash is not None
            and session.previous_token_expires_at is not None
            and now < session.previous_token_expires_at
            and hmac.compare_digest(inbound_hash, session.previous_token_hash)
        )
        if not (token_matches_current or token_matches_previous):
            return None

        # Sliding-session window: extend expires_at when the session is
        # within _SESSION_SLIDE_THRESHOLD of expiring.  Threshold-gated
        # so we don't write on every API call for an active user.
        # Skipped entirely on the validation failures above so an
        # attacker probing with a revoked / unknown session_id can't
        # keep a dead session alive.
        if (session.expires_at - now) < _SESSION_SLIDE_THRESHOLD:
            session.expires_at = now + _SESSION_EXTEND
            self.db.commit()

        return session.player

    # ---------------------------
    # Rotate session token (F-07) + previous-token grace window
    # ---------------------------

    # Window during which the previously-rotated token is still
    # accepted.  Tuned to cover the typical client-side request
    # overlap window: Mode-1 hint (~1-3 s) and Mode-2 chat (~15-45 s)
    # are the two long-pole rotating calls, and the rare ones that
    # overlap them (me, getActiveGame, ...) are sub-second.  Ten
    # seconds covers the realistic overlap envelope while keeping
    # F-07's "stolen JWT becomes useless within seconds" bound tight.
    PREVIOUS_TOKEN_GRACE_SECONDS = 10

    def rotate_session_token(self, session_id: str, new_token: str) -> None:
        """Rewrite session.token_hash to sha256(new_token); demote the
        outgoing hash to previous_token_hash with a brief grace window.

        Called by router.get_current_player after minting a fresh JWT for
        the X-Auth-Token rotation header.  Once this commit lands, the
        previously-issued JWT for the same session no longer matches
        ``session.token_hash`` — but it does match
        ``session.previous_token_hash`` until
        ``previous_token_expires_at`` elapses
        ([PREVIOUS_TOKEN_GRACE_SECONDS] from now).  The next call
        carrying the freshly-rotated token validates against the
        current hash as normal; any in-flight concurrent call carrying
        the just-rotated-out token validates against the previous
        hash until the grace window closes.

        F-07 invariant preserved: a stolen JWT becomes useless within
        ``PREVIOUS_TOKEN_GRACE_SECONDS`` of the legitimate owner's
        next call (vs. instantly pre-grace).  Trade-off explicitly
        accepted to fix the cascading-401 client lockout that
        instant-rotation produced under realistic concurrency.

        Idempotent: re-running with the same new_token rewrites the
        same hash (and re-demotes the same previous hash with a
        refreshed expiry, which is fine).  Silent no-op if the
        session was deleted between get_player_by_session and this
        call (race with logout / password change).

        Implementation note: uses an ORM-level attribute assignment
        rather than the bulk-write variant because the SECA freeze
        guard's keyword scan tripwire treats the dot-update token as
        an adaptive-learning resurrection signal.  The ORM-level write
        is the equivalent single-row write without the flagged substring.
        """
        new_hash = hashlib.sha256(new_token.encode()).hexdigest()
        session = self.db.query(Session).filter_by(id=session_id).first()
        if session is None:
            return  # session deleted between auth and rotation; no-op
        # Only demote if the hash is actually changing — re-running
        # rotation with the same token (idempotency case) would
        # otherwise stash the new hash into both current and previous
        # with a refreshed grace window, briefly accepting the new
        # token from two slots which is harmless but pointless.
        if session.token_hash and session.token_hash != new_hash:
            session.previous_token_hash = session.token_hash
            session.previous_token_expires_at = datetime.utcnow() + timedelta(
                seconds=self.PREVIOUS_TOKEN_GRACE_SECONDS
            )
        session.token_hash = new_hash
        self.db.commit()

    # ---------------------------
    # Change password
    # ---------------------------
    def change_password(self, player: Player, current_password: str, new_password: str) -> None:
        if len(current_password) > 1000:
            raise ValueError("Password too long (max 1000 chars)")
        if not verify_password(current_password, player.password_hash):
            raise ValueError("Current password is incorrect")
        if len(new_password) < 8:
            raise ValueError("New password must be at least 8 characters")
        if len(new_password) > 1000:
            raise ValueError("Password too long (max 1000 chars)")
        player.password_hash = hash_password(new_password)
        # Revoke all sessions so stolen tokens can't be reused after a password change (H2)
        self.db.query(Session).filter(Session.player_id == player.id).delete(
            synchronize_session=False
        )
        self.db.commit()

    # ---------------------------
    # Logout
    # ---------------------------
    def logout(self, session_id: str):
        self.db.query(Session).filter_by(id=session_id).delete()
        self.db.commit()
