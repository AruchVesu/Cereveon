"""
Regression tests for the JWT-rotation / session.token_hash contract.

Background
----------
[router.get_current_player] mints a fresh JWT on every successful
authenticated response and returns it via the X-Auth-Token header so
the Android client can rotate its stored token transparently.  In
production the rotation step ALSO updates session.token_hash to
sha256(new_token) via [AuthService.rotate_session_token].

Pre-F-07 (closed 2026-05-11) the rotation step did NOT update
session.token_hash, and [AuthService.get_player_by_session] did NOT
recompute sha256(token) — both halves were missing.  A stolen JWT
remained valid for its full 24 h exp window because the only
revocation lever was the session row itself (logout / change_password).

F-07 reinstates BOTH halves together:
  - rotate_session_token writes sha256(new_token) into the row
  - get_player_by_session compares sha256(inbound) against the row

Together they give per-token revocation: the just-superseded JWT no
longer validates the moment the rotation commit lands.

Pinned invariants
-----------------
AUTH_ROT_01 — rotation flow round-trips:
  1. login mints JWT_v1, login() stores sha256(JWT_v1) in token_hash
  2. router validates JWT_v1, mints JWT_v2, rotates → token_hash = sha256(JWT_v2)
  3. next call with JWT_v2 validates

AUTH_ROT_02 (NEW, F-07) — per-token revocation:
  After rotation in step 2 above, JWT_v1 no longer validates because
  its sha256 no longer matches session.token_hash.  This is the
  per-token revocation lever closed by F-07.

AUTH_ROT_03 — logout still works after rotation.

AUTH_ROT_04 — expired session still rejects rotated tokens.
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
# AUTH_ROT_01 — rotation round-trip
# ---------------------------------------------------------------------------


def test_rotation_round_trip(service, db):
    """AUTH_ROT_01 — login -> validate JWT_v1 -> rotate -> validate JWT_v2.

    Reproduces the production sequence:
      1. login() mints JWT_v1 and stores sha256(JWT_v1) in token_hash.
      2. Router validates JWT_v1 (succeeds), mints JWT_v2, calls
         rotate_session_token which writes sha256(JWT_v2) into the row.
      3. Next call with JWT_v2 must validate.
    """
    # Step 1.
    service.register("rotate@example.com", "rotate-pass-1")
    jwt_v1, login_player = service.login("rotate@example.com", "rotate-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    session_row = db.query(Session).filter_by(id=session_id).first()
    assert session_row is not None
    assert session_row.token_hash == hashlib.sha256(jwt_v1.encode()).hexdigest()

    # Step 2: JWT_v1 validates, then rotate to JWT_v2.
    player_v1 = service.get_player_by_session(session_id, jwt_v1)
    assert player_v1 is not None
    assert player_v1.id == login_player.id

    # Sleep to force exp claim to advance so JWT strings differ.
    time.sleep(1.1)
    jwt_v2 = create_access_token(player_id=str(player_v1.id), session_id=session_id)
    assert jwt_v2 != jwt_v1, "rotation must produce a different JWT string"

    service.rotate_session_token(session_id, jwt_v2)

    # After rotation the stored hash must track JWT_v2.
    db.expire_all()
    session_row = db.query(Session).filter_by(id=session_id).first()
    assert session_row.token_hash == hashlib.sha256(jwt_v2.encode()).hexdigest(), (
        "rotate_session_token must update session.token_hash to track the "
        "newly-issued JWT — otherwise the next call with JWT_v2 will 401."
    )

    # Step 3: JWT_v2 validates on the NEXT request.
    player_v2 = service.get_player_by_session(session_id, jwt_v2)
    assert player_v2 is not None, (
        "AUTH_ROT_01 violated: rotated JWT failed get_player_by_session.  "
        "The rotation step in router.get_current_player must call "
        "rotate_session_token after minting the new JWT."
    )
    assert player_v2.id == login_player.id


# ---------------------------------------------------------------------------
# AUTH_ROT_02 — per-token revocation (F-07)
# ---------------------------------------------------------------------------


def test_old_token_revoked_after_rotation(service, db):
    """AUTH_ROT_02 — closes the F-07 stolen-JWT-lives-until-exp gap,
    bounded by the [AuthService.PREVIOUS_TOKEN_GRACE_SECONDS] window.

    After the router rotates token_hash to sha256(JWT_v2), the
    previously-issued JWT_v1 must no longer validate even though its
    JWT signature is still cryptographically valid and its exp claim
    has not yet elapsed.  Per-token revocation is the F-07 lever.

    Grace-window caveat (added with the rotation-race fix): within
    [PREVIOUS_TOKEN_GRACE_SECONDS] of rotation, JWT_v1 IS still
    accepted via the previous_token_hash slot — that's the
    load-bearing fix for the concurrent-request cascade.  This test
    force-expires the grace window before asserting revocation, so
    the assertion still pins the "stolen JWT becomes useless within
    seconds" property without depending on wall-clock sleep.
    """
    from llm.seca.auth.models import Session as DbSession
    from datetime import datetime, timedelta

    service.register("revoke@example.com", "revoke-pass-1")
    jwt_v1, player = service.login("revoke@example.com", "revoke-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    # JWT_v1 validates immediately after login.
    assert service.get_player_by_session(session_id, jwt_v1) is not None

    # Rotate — exactly what router.get_current_player does after a
    # successful validation.
    time.sleep(1.1)  # advance exp so JWT strings differ
    jwt_v2 = create_access_token(player_id=str(player.id), session_id=session_id)
    service.rotate_session_token(session_id, jwt_v2)

    # JWT_v2 still validates (sanity check).
    assert service.get_player_by_session(session_id, jwt_v2) is not None

    # Force-expire the previous-token grace window so we can assert
    # the post-grace revocation contract without waiting 10 s of
    # wall-clock time.
    row = db.query(DbSession).filter_by(id=session_id).first()
    row.previous_token_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    # JWT_v1 is now stale AND outside the grace window — must be rejected.
    assert service.get_player_by_session(session_id, jwt_v1) is None, (
        "AUTH_ROT_02 violated: after rotation AND grace expiry, the "
        "superseded JWT still validated.  rotate_session_token must "
        "update token_hash AND demote the old hash with a bounded "
        "expiry; get_player_by_session must reject previous-hash matches "
        "once previous_token_expires_at is in the past.  Without all "
        "three, the F-07 per-token revocation gap reopens."
    )


def test_get_player_rejects_tampered_token(service, db):
    """AUTH_ROT_02 extension — a token that wasn't issued by the
    server (e.g. attacker-forged garbage that somehow has the right
    session_id field) must fail the hash check.

    The JWT signature check in router.decode_token would normally
    catch this upstream, but the service layer is defence-in-depth.
    """
    service.register("tamper@example.com", "tamper-pass-1")
    jwt_v1, _ = service.login("tamper@example.com", "tamper-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    assert service.get_player_by_session(session_id, "garbage-token") is None
    assert service.get_player_by_session(session_id, jwt_v1 + "-tampered") is None


def test_get_player_rejects_session_with_null_token_hash(service, db):
    """A legacy/manually-poked session row with NULL token_hash must
    fail closed — the F-07 hash check has no anchor to compare against
    and we prefer a one-time re-login over a silently-skipped check."""
    service.register("legacy@example.com", "legacy-pass-1")
    jwt_v1, _ = service.login("legacy@example.com", "legacy-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    # Manually wipe the hash to simulate a legacy row.
    session_row = db.query(Session).filter_by(id=session_id).first()
    session_row.token_hash = None
    db.commit()

    assert service.get_player_by_session(session_id, jwt_v1) is None, (
        "session row with NULL token_hash should fail closed; otherwise "
        "the per-token revocation gate has a bypass for legacy rows."
    )


# ---------------------------------------------------------------------------
# AUTH_ROT_03 / AUTH_ROT_04 — orthogonal revocation levers still work
# ---------------------------------------------------------------------------


def test_rotated_token_then_logout_revokes_session(service, db):
    """AUTH_ROT_03 — logout still kills any rotated token immediately."""
    service.register("logout@example.com", "logout-pass-1")
    jwt_v1, player = service.login("logout@example.com", "logout-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    time.sleep(1.1)
    jwt_v2 = create_access_token(player_id=str(player.id), session_id=session_id)
    service.rotate_session_token(session_id, jwt_v2)
    assert service.get_player_by_session(session_id, jwt_v2) is not None

    service.logout(session_id)

    assert service.get_player_by_session(session_id, jwt_v2) is None, (
        "logout failed to revoke a rotated token — session-row deletion "
        "is the unconditional revocation lever."
    )


def test_rotated_token_after_session_expiry_rejected(service, db):
    """AUTH_ROT_04 — expires_at still gates rotated tokens."""
    service.register("expire@example.com", "expire-pass-1")
    jwt_v1, player = service.login("expire@example.com", "expire-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    # Force-expire the session.
    session_row = db.query(Session).filter_by(id=session_id).first()
    session_row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    time.sleep(1.1)
    jwt_v2 = create_access_token(player_id=str(player.id), session_id=session_id)
    # Rotation against an expired row is a no-op-shaped UPDATE (the
    # WHERE matches the row but the row is dead anyway).
    service.rotate_session_token(session_id, jwt_v2)

    assert service.get_player_by_session(session_id, jwt_v2) is None, (
        "expired session must reject any token, rotated or not — "
        "expires_at is the staleness lever."
    )


def test_rotate_session_token_on_deleted_session_is_silent_noop(service, db):
    """AUTH_ROT_05 (Sprint 6.C) — pin the deleted-session race.

    Sequence:
      1. login mints JWT_v1 and creates the session row.
      2. Logout deletes the session row before the rotation step lands.
      3. rotate_session_token(session_id, new_token) must silently
         no-op rather than raise — the row that would have been
         updated is gone, but raising would surface a transient 500
         to the user when the only "wrong" state is "got logged out
         elsewhere simultaneously."  Documented behaviour in
         ``AuthService.rotate_session_token``.
    """
    service.register("race@example.com", "race-pass-1")
    jwt_v1, _player = service.login("race@example.com", "race-pass-1")
    session_id = decode_token(jwt_v1)["session_id"]

    # Confirm the row exists, then delete it via logout.
    assert db.query(Session).filter_by(id=session_id).first() is not None
    service.logout(session_id)
    assert db.query(Session).filter_by(id=session_id).first() is None

    # Rotation against a missing row must not raise — proves the
    # ``if session is None: return`` early-out fires.
    service.rotate_session_token(session_id, "would-be-new-token")

    # And the row is still gone — rotation didn't accidentally
    # resurrect it via the create_or_update pattern.
    assert db.query(Session).filter_by(id=session_id).first() is None
