import functools
import hashlib
import hmac
from datetime import datetime, timedelta
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

        return token, player

    # ---------------------------
    # Validate session
    # ---------------------------
    def get_player_by_session(self, session_id: str, token: str) -> Player | None:
        session = self.db.query(Session).filter_by(id=session_id).first()
        if not session:
            return None

        # Fail-closed: treat missing expiry as expired (M1)
        now = datetime.utcnow()
        if session.expires_at is None or session.expires_at < now:
            return None

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(token_hash, session.token_hash or ""):
            return None

        # Sliding-session window: extend expires_at when the session is
        # within _SESSION_SLIDE_THRESHOLD of expiring.  Threshold-gated
        # so we don't write on every API call for an active user.
        # Skipped entirely on validation failure above so an attacker
        # probing with a stolen-then-revoked token can't keep a dead
        # session alive.
        if (session.expires_at - now) < _SESSION_SLIDE_THRESHOLD:
            session.expires_at = now + _SESSION_EXTEND
            self.db.commit()

        return session.player

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
        self.db.query(Session).filter(
            Session.player_id == player.id
        ).delete(synchronize_session=False)
        self.db.commit()

    # ---------------------------
    # Logout
    # ---------------------------
    def logout(self, session_id: str):
        self.db.query(Session).filter_by(id=session_id).delete()
        self.db.commit()
