"""
Backend tests for the X-Auth-Token refresh header issued by the
auth-rotation pair: ``get_current_player`` (dep) queues a pending
rotation on ``request.state``; ``commit_pending_auth_rotation``
(middleware in ``llm/server.py``) commits it and sets the header
only on 2xx responses.

Background
----------
Pre-this-feature:
  - Sliding session (2026-04): JWT exp = 7 days, server slides
    session.expires_at on each authenticated request.  No JWT
    refresh — JWT eventually expires regardless of activity.

Then:
  - get_current_player attached a fresh X-Auth-Token header on every
    authenticated call (rotation committed inside the dep, BEFORE the
    route handler ran).  Combined with F-07 per-token revocation,
    any 5xx from the route handler revoked the old token without
    delivering the new one to the client — issue #130's cascade
    lockout.

Now (issue #130 fix):
  - The dep mints a new JWT and stashes ``(session_id, new_token)`` on
    ``request.state.pending_auth_rotation``.
  - A FastAPI HTTP middleware commits the DB rotation + sets
    ``X-Auth-Token`` only when the response is 2xx.  5xx / 4xx routes
    discard the pending rotation; the previously-presented token
    remains valid for the user's next call.
  - JWT exp stays at 24 h.  Daily-active users keep refreshing;
    a stolen JWT is valid only for 24 h regardless of server-side
    session state.

Pinned invariants
-----------------
 1. REFRESH_HEADER_PRESENT_ON_SUCCESS: every successful authenticated
    cycle (dep + middleware-commit) sets X-Auth-Token on the response.
 2. REFRESH_TOKEN_IS_VALID_JWT: the returned token decodes successfully
    via the same secret key.
 3. REFRESH_TOKEN_BINDS_SAME_SESSION: the new token's session_id
    matches the inbound token's session_id (we don't rotate sessions
    on every call, just JWTs).
 4. REFRESH_TOKEN_BINDS_SAME_PLAYER: the new token's player_id
    matches the authenticated player's id.
 5. REFRESH_TOKEN_HAS_FUTURE_EXP: the new token's exp claim is in
    the future (defensive against clock-skew bugs).
 6. NO_REFRESH_ON_INVALID_TOKEN: an attacker probing with a
    bogus / revoked token does NOT get back a fresh JWT (would
    let them keep guessing indefinitely).
 7. JWT_EXP_AT_TIGHT_24H: ACCESS_EXPIRE_MINUTES is back to 24 h.
 8. CASCADE_NO_LOCKOUT_ON_5XX (#130): when the route handler 5xxs,
    the middleware skips the rotation commit so the same JWT stays
    valid for the user's next call.
 9. CASCADE_NO_LOCKOUT_ON_4XX (#130): same property for handler-
    raised HTTPException (4xx).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.auth.models import Base
from llm.seca.auth.router import get_current_player
from llm.seca.auth.service import AuthService
from llm.seca.auth.tokens import ACCESS_EXPIRE_MINUTES, decode_token


def _simulate_2xx_middleware_commit(request: Request, response: Response, db) -> None:
    """Replay the production ``commit_pending_auth_rotation`` middleware
    for a successful (2xx) response cycle.

    Production split: the dep stashes the pending rotation; the
    middleware in ``server.py`` commits it + sets ``X-Auth-Token`` only
    on 2xx.  Tests that call the dep directly need an equivalent step
    to observe the rotation's effect — calling this helper after the
    dep run does exactly the same work the middleware does on success.
    Skipping this helper (or calling it conditionally) mirrors the 4xx
    / 5xx skip path used by the cascade-prevention tests below.
    """
    pending = getattr(request.state, "pending_auth_rotation", None)
    if pending is None:
        return
    AuthService(db).rotate_session_token(pending["session_id"], pending["new_token"])
    response.headers["X-Auth-Token"] = pending["new_token"]


# ---------------------------------------------------------------------------
# 1. ACCESS_EXPIRE_MINUTES tightened back to 24 h
# ---------------------------------------------------------------------------


def test_jwt_exp_back_to_24h_now_that_refresh_exists():
    """JWT_EXP_AT_TIGHT_24H — sliding session pushed this to 7d as a
    safety fallback for the no-refresh era.  With X-Auth-Token
    refresh on every authenticated response, 24 h is the correct
    security/UX trade-off again."""
    assert ACCESS_EXPIRE_MINUTES == 60 * 24, (
        "ACCESS_EXPIRE_MINUTES drifted from the 24 h target.  If you're "
        "intentionally widening it, also drop the X-Auth-Token refresh "
        "(the two together are the security/UX contract)."
    )


# ---------------------------------------------------------------------------
# 2. get_current_player behaviour — direct dependency call
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session with the auth schema; torn down after each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _login(db, email="refresh@test.com", pw="refresh-pass-1") -> str:
    """Register + login, return the JWT.  Goes through the real
    AuthService so the session row is exactly what the router would
    see in production."""
    service = AuthService(db)
    service.register(email, pw)
    token, _ = service.login(email, pw, device_info="test")
    return token


def _call_get_current_player(
    token: str, db, simulate_success: bool = True
) -> tuple[Response, object]:
    """Invoke the dependency directly + replay the
    ``commit_pending_auth_rotation`` middleware as a 2xx response would
    in production.

    Pre-#130 the dep itself committed the rotation + set the response
    header; tests could assert on ``response.headers`` immediately.
    Post-#130 the dep only queues the rotation; the middleware commits
    on success.  This helper preserves the existing "after a successful
    dep call, response.headers has X-Auth-Token" assertion surface by
    calling [_simulate_2xx_middleware_commit] for callers in the
    success path.

    Pass ``simulate_success=False`` to skip the commit (used by the
    cascade-prevention tests that need to observe the dep-queued-but-
    not-committed state — the production equivalent of a 5xx response).
    """
    response = Response()
    request = Request(scope={"type": "http", "headers": []})
    player = get_current_player(
        response=response,
        authorization=f"Bearer {token}",
        db=db,
        request=request,
    )
    if simulate_success:
        _simulate_2xx_middleware_commit(request, response, db)
    return response, player


# ---------------------------------------------------------------------------
# 3. Header behaviour
# ---------------------------------------------------------------------------


class TestRefreshHeaderOnSuccess:
    """Every successful authenticated response carries X-Auth-Token."""

    def test_header_set_on_success(self, db):
        """REFRESH_HEADER_PRESENT_ON_SUCCESS."""
        token = _login(db)
        response, player = _call_get_current_player(token, db)
        assert "x-auth-token" in {
            k.lower() for k in response.headers.keys()
        }, f"missing X-Auth-Token, got headers: {list(response.headers.keys())}"

    def test_refresh_token_decodes_as_valid_jwt(self, db):
        """REFRESH_TOKEN_IS_VALID_JWT."""
        token = _login(db)
        response, _ = _call_get_current_player(token, db)
        new_token = response.headers["x-auth-token"]
        # Should decode without raising.
        payload = decode_token(new_token)
        assert "player_id" in payload
        assert "session_id" in payload
        assert "exp" in payload

    def test_refresh_token_binds_same_session(self, db):
        """REFRESH_TOKEN_BINDS_SAME_SESSION — the new token continues
        the same server-side session, doesn't open a new one."""
        original = _login(db)
        original_session = decode_token(original)["session_id"]

        response, _ = _call_get_current_player(original, db)
        new_session = decode_token(response.headers["x-auth-token"])["session_id"]

        assert new_session == original_session, (
            "refreshed JWT must continue the original session — "
            f"got original={original_session} new={new_session}"
        )

    def test_refresh_token_binds_same_player(self, db):
        """REFRESH_TOKEN_BINDS_SAME_PLAYER."""
        original = _login(db)
        original_player = decode_token(original)["player_id"]

        response, player = _call_get_current_player(original, db)
        new_player = decode_token(response.headers["x-auth-token"])["player_id"]

        assert new_player == original_player == str(player.id)

    def test_refresh_token_has_future_exp(self, db):
        """REFRESH_TOKEN_HAS_FUTURE_EXP — defensive against clock-skew
        / off-by-one bugs that could issue an immediately-expired
        token.  Use time.time() (always Unix epoch / UTC) rather than
        datetime.utcnow().timestamp() — the latter treats a naive
        datetime as local TZ and would skew this assertion by the
        runner's TZ offset on non-UTC machines."""
        token = _login(db)
        response, _ = _call_get_current_player(token, db)
        exp = decode_token(response.headers["x-auth-token"])["exp"]
        now = time.time()
        assert exp > now + 60, "refresh token expires in less than a minute — clock-skew bug?"
        assert exp < now + 60 * 60 * 25, (
            f"refresh token expires {(exp - now) / 3600:.1f}h out — exceeds the "
            "documented 24 h ACCESS_EXPIRE_MINUTES"
        )


class TestNoRefreshOnFailure:
    """Failure paths must NOT leak a fresh JWT — that would let an
    attacker harvest valid tokens by probing with junk."""

    def _assert_no_header_on_response(self, response: Response):
        assert "x-auth-token" not in {
            k.lower() for k in response.headers.keys()
        }, f"failure path leaked X-Auth-Token, got: {list(response.headers.keys())}"

    def test_no_header_on_invalid_bearer(self, db):
        """NO_REFRESH_ON_INVALID_TOKEN."""
        with pytest.raises(HTTPException) as exc:
            _call_get_current_player("not-a-real-token", db)
        assert exc.value.status_code == 401
        # Response object isn't returned (HTTPException raised before
        # return), so there's no header to check — but the contract
        # is that no token was minted, which is implied by the early
        # raise.

    def test_no_header_on_malformed_authorization(self, db):
        with pytest.raises(HTTPException):
            response = Response()
            request = Request(scope={"type": "http", "headers": []})
            get_current_player(
                response=response,
                authorization="Basic dXNlcjpwdw==",  # not Bearer
                db=db,
                request=request,
            )

    def test_no_header_on_expired_session(self, db):
        """An expired server-side session must fail closed.  Sliding
        only ever moves expiry forward from a still-valid state;
        once past the deadline, the session is dead."""
        token = _login(db)
        session_id = decode_token(token)["session_id"]

        # Manually expire the session row.
        from llm.seca.auth.models import Session as DbSession

        row = db.query(DbSession).filter_by(id=session_id).first()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()

        with pytest.raises(HTTPException) as exc:
            _call_get_current_player(token, db)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# 4. F-07 — per-token revocation through the router
# ---------------------------------------------------------------------------


def _expire_grace_window(db, session_id: str) -> None:
    """Force the previous-token grace window to be in the past so
    tests can assert post-grace revocation behaviour without sleeping
    PREVIOUS_TOKEN_GRACE_SECONDS in wall time.  Mirrors how
    test_no_header_on_expired_session forces an expired session row.
    """
    from llm.seca.auth.models import Session as DbSession

    row = db.query(DbSession).filter_by(id=session_id).first()
    if row is not None:
        row.previous_token_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()


class TestF07PerTokenRevocation:
    """End-to-end revocation through the router dependency.

    Pre-F-07 a stolen JWT remained valid until the 24 h exp claim ran out
    because rotate_session_token didn't exist and get_player_by_session
    didn't compare hashes.  Post-F-07 the rotation step writes
    sha256(new_token) into the session row, so any previously-issued JWT
    for the same session fails its next router call — after the
    [AuthService.PREVIOUS_TOKEN_GRACE_SECONDS] grace window elapses
    (see TestF07GraceWindow below for the in-window behaviour).
    """

    def test_old_token_rejected_after_grace_window(self, db):
        """F07_OLD_TOKEN_REVOKED — present JWT_v1 once (router rotates
        to JWT_v2), force-expire the grace window, then present JWT_v1
        again.  The second call must 401 — that's the per-token
        revocation lever.

        Sleep between login and the first router call so the rotated
        JWT genuinely differs as a string.  Without this, login and
        the router call land in the same Unix second; the exp claim
        is identical; create_access_token is deterministic; and the
        "rotation" produces a byte-identical token that the test
        cannot distinguish.  Production calls are separated by at
        least the network RTT, so this is a unit-test artefact, not
        a contract gap.
        """
        token_v1 = _login(db)
        time.sleep(1.1)
        response, _ = _call_get_current_player(token_v1, db)
        token_v2 = response.headers["x-auth-token"]
        assert token_v2 != token_v1, "rotation must mint a different token"

        # Force the grace window to be in the past so we test the
        # post-grace revocation path without burning 10 s.
        session_id = decode_token(token_v1)["session_id"]
        _expire_grace_window(db, session_id)

        # Same JWT_v1 presented again — grace expired, must 401.
        with pytest.raises(HTTPException) as exc:
            _call_get_current_player(token_v1, db)
        assert exc.value.status_code == 401, (
            "F-07 violated: previously-rotated JWT still validated after "
            "grace expired.  router.get_current_player must call "
            "rotate_session_token after issuing the X-Auth-Token header, "
            "and get_player_by_session must reject previous_token_hash "
            "matches once previous_token_expires_at is in the past."
        )

    def test_rotated_token_validates_then_rotates_again(self, db):
        """F07_NEW_TOKEN_WORKS — sanity: the freshly-issued JWT_v2
        validates on the next call AND triggers another rotation to
        JWT_v3.  The rotation chain keeps advancing as long as the
        client keeps the latest token."""
        token_v1 = _login(db)
        time.sleep(1.1)
        response1, _ = _call_get_current_player(token_v1, db)
        token_v2 = response1.headers["x-auth-token"]

        # JWT_v2 validates and produces JWT_v3.
        time.sleep(1.1)
        response2, _ = _call_get_current_player(token_v2, db)
        token_v3 = response2.headers["x-auth-token"]
        assert token_v3 != token_v2 != token_v1

        # And the chain continues: JWT_v2 is now in the previous slot.
        # Expire the grace window and confirm it no longer validates —
        # mirrors the post-grace revocation path on JWT_v1 above.
        session_id = decode_token(token_v1)["session_id"]
        _expire_grace_window(db, session_id)
        with pytest.raises(HTTPException) as exc:
            _call_get_current_player(token_v2, db)
        assert exc.value.status_code == 401, (
            "rotation chain broken: a JWT that was just rotated past "
            "should be rejected on its next presentation after grace expires."
        )


# ---------------------------------------------------------------------------
# 4b. F-07 grace window — accepts the previous token within the grace,
#                        rejects it after.
# ---------------------------------------------------------------------------


class TestF07GraceWindow:
    """Pin the grace-window behaviour added to absorb the rotation race.

    Background: pre-grace, two concurrent authenticated requests with
    the same starting JWT both reached the server.  The first rotated
    the token_hash; the second arrived after rotation and 401'd as
    if revoked, even though it was the legitimate owner's
    request — and the client's cascade then locked the session.

    Fix: rotate_session_token demotes the outgoing hash to
    previous_token_hash with an expiry
    AuthService.PREVIOUS_TOKEN_GRACE_SECONDS in the future.  Within
    that window, get_player_by_session accepts either current or
    previous.  After it, only current.
    """

    def test_previous_token_accepted_within_grace(self, db):
        """GRACE_PREVIOUS_ACCEPTED_WITHIN_WINDOW — JWT_v1 still
        validates immediately after the rotation to JWT_v2.  This is
        the load-bearing contract that fixes the concurrent-rotation
        cascade lockout."""
        token_v1 = _login(db)
        time.sleep(1.1)
        # First call: rotates to JWT_v2.
        _call_get_current_player(token_v1, db)

        # JWT_v1 is now in previous_token_hash with a grace window of
        # PREVIOUS_TOKEN_GRACE_SECONDS.  Present it again — must
        # validate, NOT 401.
        response, player = _call_get_current_player(token_v1, db)
        assert player is not None, (
            "Previous token within grace must validate.  Without this, "
            "concurrent rotating requests on the same starting token "
            "cause the cascading-401 client lockout."
        )
        # And rotates again — the X-Auth-Token returned here is
        # whatever the helper's middleware-simulate yielded; we don't
        # pin the specific value, only that the call succeeded.
        assert "x-auth-token" in {k.lower() for k in response.headers.keys()}

    def test_previous_token_rejected_after_grace(self, db):
        """GRACE_PREVIOUS_REJECTED_AFTER_WINDOW — once
        previous_token_expires_at is in the past, the previous token
        is no longer accepted; F-07 revocation is fully in force."""
        token_v1 = _login(db)
        time.sleep(1.1)
        _call_get_current_player(token_v1, db)  # rotates to JWT_v2

        # Force grace to be in the past.
        session_id = decode_token(token_v1)["session_id"]
        _expire_grace_window(db, session_id)

        with pytest.raises(HTTPException) as exc:
            _call_get_current_player(token_v1, db)
        assert exc.value.status_code == 401, (
            "Grace window must NOT extend revocation past "
            "previous_token_expires_at.  After grace, the previous "
            "token is fully revoked — that's the F-07 guarantee."
        )

    def test_concurrent_rotation_both_calls_succeed(self, db):
        """GRACE_CONCURRENT_ROTATION — simulate two near-simultaneous
        authenticated calls carrying the SAME starting token (the
        production race the grace window fixes).  Both must succeed:
        the first rotates v1→v2 and demotes v1 to previous; the
        second presents v1, matches previous, and rotates again
        (this time previous→v2's predecessor, i.e. v1 re-demoted
        with a refreshed grace).  Neither 401s.
        """
        token_v1 = _login(db)
        time.sleep(1.1)

        # First call rotates to v2.
        response_a, player_a = _call_get_current_player(token_v1, db)
        assert player_a is not None

        # Simulated second call from a concurrent request that still
        # carries v1 (it started before A's rotation committed).
        # Must NOT 401.
        response_b, player_b = _call_get_current_player(token_v1, db)
        assert player_b is not None, (
            "Concurrent request with the previous token must validate "
            "inside the grace window — this is the whole point of the "
            "grace window (it fixes the cascading-401 lockout)."
        )
        # Both calls receive a new X-Auth-Token via the middleware
        # simulation; we don't pin the specific values, only that
        # both succeeded and got refreshed headers.
        assert "x-auth-token" in {k.lower() for k in response_a.headers.keys()}
        assert "x-auth-token" in {k.lower() for k in response_b.headers.keys()}


# ---------------------------------------------------------------------------
# 5. #130 — cascade prevention: non-2xx routes do NOT consume the token
# ---------------------------------------------------------------------------


class TestCascadePreventionOnNon2xx:
    """Pin the issue-#130 fix: when the route handler returns anything
    other than 2xx, the middleware MUST NOT commit the queued rotation.

    Pre-#130 the rotation was committed inside ``get_current_player``
    before the route handler ran.  Any subsequent route 5xx (e.g.
    issue #129's borderline LLM hint being killed by the boundary
    validator) revoked the old token without delivering the new one
    to the client → user locked out until re-login.

    Post-#130 the dep only queues the rotation on
    ``request.state.pending_auth_rotation``; the ``commit_pending_auth_rotation``
    middleware in ``server.py`` checks the response status and only
    commits on 2xx.  The tests below exercise the success-skip path by
    calling the helper with ``simulate_success=False`` — exactly the
    work the middleware would (not) do on 4xx / 5xx.
    """

    def test_5xx_response_leaves_old_token_valid(self, db):
        """CASCADE_NO_LOCKOUT_ON_5XX — first call's route 500s.  The
        same JWT must still validate on the next call because the
        rotation was never committed."""
        token_v1 = _login(db)

        # First call: dep runs (mints + queues), route "5xxs" → middleware
        # skip → no commit.  Use simulate_success=False to mirror that path.
        time.sleep(1.1)
        response_1, _ = _call_get_current_player(token_v1, db, simulate_success=False)
        assert "x-auth-token" not in {
            k.lower() for k in response_1.headers.keys()
        }, "5xx response leaked X-Auth-Token — middleware skip is the contract"

        # Second call: same JWT, route succeeds.  Must still validate.
        response_2, player = _call_get_current_player(token_v1, db)
        assert (
            player is not None
        ), "token_v1 was revoked after a 5xx — cascade lockout regressed (#130)"

    def test_4xx_response_leaves_old_token_valid(self, db):
        """CASCADE_NO_LOCKOUT_ON_4XX — same property for handler-raised
        HTTPException paths (e.g. a route returning 422 on bad input)."""
        token_v1 = _login(db)

        time.sleep(1.1)
        # Same simulation: dep ran but middleware skipped because the
        # route was 4xx.
        response_1, _ = _call_get_current_player(token_v1, db, simulate_success=False)
        assert "x-auth-token" not in {k.lower() for k in response_1.headers.keys()}

        # Token still works.
        _, player = _call_get_current_player(token_v1, db)
        assert (
            player is not None
        ), "token_v1 was revoked after a 4xx — cascade lockout regressed (#130)"

    def test_consecutive_5xx_does_not_compound_lockout(self, db):
        """CASCADE_REPEATED_5XX — even multiple consecutive 5xx
        responses leave the same JWT valid.  Pre-#130 the FIRST 5xx
        was enough; this is a defense-in-depth pin."""
        token_v1 = _login(db)

        # Three consecutive "5xx" simulations.
        for _ in range(3):
            time.sleep(0.05)
            _call_get_current_player(token_v1, db, simulate_success=False)

        # Token survives.
        _, player = _call_get_current_player(token_v1, db)
        assert player is not None
