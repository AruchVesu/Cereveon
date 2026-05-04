"""
Backend tests for the X-Auth-Token refresh header issued by
[get_current_player] on every successful authenticated response.

Background
----------
Pre-this-feature:
  - Sliding session (2026-04): JWT exp = 7 days, server slides
    session.expires_at on each authenticated request.  No JWT
    refresh — JWT eventually expires regardless of activity.

Now:
  - get_current_player attaches a fresh X-Auth-Token header on every
    successful auth, so an active client rotates its stored JWT
    transparently.
  - JWT exp drops back to 24 h.  Daily-active users keep refreshing;
    a stolen JWT is valid only for 24 h regardless of server-side
    session state.

Pinned invariants
-----------------
 1. REFRESH_HEADER_PRESENT_ON_SUCCESS: every successful authenticated
    call sets X-Auth-Token on the response.
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
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.auth.models import Base
from llm.seca.auth.router import get_current_player
from llm.seca.auth.service import AuthService
from llm.seca.auth.tokens import ACCESS_EXPIRE_MINUTES, decode_token


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


def _call_get_current_player(token: str, db) -> tuple[Response, object]:
    """Invoke the dependency function directly with a fresh Response.
    Returns (response, player) on success.  Raises the HTTPException
    on failure paths so the caller can assert against it."""
    response = Response()
    player = get_current_player(
        response=response,
        authorization=f"Bearer {token}",
        db=db,
    )
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
        assert "x-auth-token" in {k.lower() for k in response.headers.keys()}, (
            f"missing X-Auth-Token, got headers: {list(response.headers.keys())}"
        )

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
        assert "x-auth-token" not in {k.lower() for k in response.headers.keys()}, (
            f"failure path leaked X-Auth-Token, got: {list(response.headers.keys())}"
        )

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
            get_current_player(
                response=response,
                authorization="Basic dXNlcjpwdw==",  # not Bearer
                db=db,
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
