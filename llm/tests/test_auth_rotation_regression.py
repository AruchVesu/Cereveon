"""
Regression test for the JWT-rotation / session.token_hash mismatch bug
that silently broke production from the second authenticated call
onward.

Background
----------
[router.get_current_player] mints a fresh JWT on every successful
authenticated response and returns it via the X-Auth-Token header so
the Android client can rotate its stored token transparently.  The
sister method [AuthService.get_player_by_session] used to recompute
sha256(token) and compare it against session.token_hash with
hmac.compare_digest — but rotation never updates session.token_hash,
so the second authenticated call always failed the strict check and
returned 401.  Production was silently broken from the moment the
client persisted the rotated token.

Pinned invariant
----------------
AUTH_ROT_01 — a JWT minted with the same session_id as the one
recorded at login() must validate via get_player_by_session even
though its sha256 hash does NOT match session.token_hash.  This is
the contract that the rotation feature in get_current_player relies
on; if a future change re-introduces the strict hash check, the
rotation feature will break again and this test will catch it
before it ships.

Why the trade-off is acceptable
-------------------------------
The JWT signature itself authenticates the bearer (verified at the
router boundary by decode_token).  Server-side session lifecycle is
controlled by:
  - row deletion: logout, password change, expiry-prune in login
  - expires_at: 7-day sliding window, fail-closed on past-deadline
  - JWT exp: 24 h hard cap on any individual token

Per-token revocation (revoke a single leaked JWT without killing the
session) is no longer possible — but it never worked correctly in the
presence of rotation, so nothing has been lost.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.auth.models import Base, Session
from llm.seca.auth.service import AuthService
from llm.seca.auth.tokens import create_access_token, decode_token


# ---------------------------------------------------------------------------
# Fixtures
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
        Base.metadata.drop_all(engine)


@pytest.fixture()
def service(db):
    return AuthService(db)


# ---------------------------------------------------------------------------
# AUTH_ROT_01
# ---------------------------------------------------------------------------


def test_rotated_token_validates_on_next_request(service, db):
    """AUTH_ROT_01 — pin the contract that JWT rotation depends on.

    Reproduces the production sequence:
      1. Client logs in -> AuthService.login mints JWT_v1 and stores
         sha256(JWT_v1) in session.token_hash.
      2. Client makes its first authenticated call.  Router runs
         get_current_player, validates via get_player_by_session
         (succeeds, hashes match), then mints JWT_v2 and returns it
         via X-Auth-Token.  Client persists JWT_v2.
      3. Client makes its NEXT authenticated call with JWT_v2.
         Router validates via get_player_by_session.  This is the
         step that used to 401 because sha256(JWT_v2) != token_hash
         (the row still holds sha256(JWT_v1)).

    The contract pinned here: step 3 MUST succeed.
    """
    # Step 1: login — produces JWT_v1 + a session row.
    service.register("rotate@example.com", "rotate-pass-1")
    jwt_v1, login_player = service.login("rotate@example.com", "rotate-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    # Sanity: the row was actually written.
    session_row = db.query(Session).filter_by(id=session_id).first()
    assert session_row is not None, "login must create a session row"
    original_token_hash = session_row.token_hash
    assert original_token_hash, "login must set session.token_hash for the initial JWT"

    # Step 2: first authenticated call — JWT_v1 still validates.
    player_v1 = service.get_player_by_session(session_id, jwt_v1)
    assert player_v1 is not None, "JWT_v1 must validate immediately after login"
    assert player_v1.email == "rotate@example.com"
    assert player_v1.id == login_player.id

    # Simulate what router.get_current_player does after a successful
    # validation: mint a fresh JWT for the SAME session_id.  This is
    # the rotation step.  Critically, session.token_hash is NOT
    # updated — the bug was assuming it would be.
    #
    # In production, login and the next authenticated request are
    # separated by hundreds of ms at minimum, so the exp claims of
    # JWT_v1 and JWT_v2 differ and so do their sha256s.  In a unit
    # test both calls land in the same second, so we sleep just over
    # one second to force the exp claim (Unix-second-resolution) to
    # advance and produce a genuinely different JWT string.  Without
    # this guard the test would silently pass on a token-equality
    # accident even if the strict hash check came back.
    time.sleep(1.1)
    jwt_v2 = create_access_token(player_id=str(player_v1.id), session_id=session_id)
    assert jwt_v2 != jwt_v1, (
        "rotation must produce a different JWT string — sleep window "
        "wasn't long enough to advance the exp claim"
    )
    assert hashlib.sha256(jwt_v2.encode()).hexdigest() != original_token_hash, (
        "rotated JWT must hash differently from the stored token_hash — "
        "otherwise the strict-check failure mode this test pins doesn't apply"
    )

    # Confirm the row's token_hash was not silently updated by
    # something else — this test is meaningless if it was.
    db.expire_all()
    session_row = db.query(Session).filter_by(id=session_id).first()
    assert session_row.token_hash == original_token_hash, (
        "session.token_hash must NOT track rotated JWTs — if it does, "
        "this test no longer pins the rotation contract.  Investigate."
    )

    # Step 3: the regression check.  The rotated JWT, paired with the
    # ORIGINAL session_id, must validate.  Pre-fix this returned None
    # (the strict sha256 check failed) and every authenticated call
    # after the first 401'd in production.
    player_v2 = service.get_player_by_session(session_id, jwt_v2)
    assert player_v2 is not None, (
        "AUTH_ROT_01 violated: rotated JWT failed get_player_by_session.  "
        "If the strict sha256(token) ?= session.token_hash check was "
        "re-introduced, the rotation feature in router.get_current_player "
        "will break in production again."
    )
    assert player_v2.email == "rotate@example.com"
    assert player_v2.id == login_player.id


def test_rotated_token_then_logout_revokes_session(service, db):
    """AUTH_ROT_01 corollary — dropping the per-token check did NOT
    break session-level revocation.  Logout still kills any rotated
    token immediately, because logout deletes the session row and
    get_player_by_session early-returns when the row is missing."""
    service.register("revoke@example.com", "revoke-pass-1")
    jwt_v1, player = service.login("revoke@example.com", "revoke-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    # Rotate.
    jwt_v2 = create_access_token(player_id=str(player.id), session_id=session_id)
    assert service.get_player_by_session(session_id, jwt_v2) is not None

    # Logout.
    service.logout(session_id)

    # Rotated token must no longer validate.
    assert service.get_player_by_session(session_id, jwt_v2) is None, (
        "logout failed to revoke a rotated token — session-level "
        "revocation is the only revocation lever left after AUTH_ROT_01."
    )


def test_rotated_token_after_session_expiry_rejected(service, db):
    """AUTH_ROT_01 corollary — expires_at still gates rotated tokens.
    A rotated JWT used against an expired session row must be
    rejected."""
    service.register("expire@example.com", "expire-pass-1")
    jwt_v1, player = service.login("expire@example.com", "expire-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    # Force-expire the session.
    session_row = db.query(Session).filter_by(id=session_id).first()
    session_row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    # Rotate AFTER expiry — JWT itself is fresh but the session row
    # is dead.
    jwt_v2 = create_access_token(player_id=str(player.id), session_id=session_id)

    assert service.get_player_by_session(session_id, jwt_v2) is None, (
        "expired session must reject any token, rotated or not — "
        "expires_at is the staleness lever after AUTH_ROT_01."
    )
