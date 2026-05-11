"""Unit tests for AuthService: register, login, session validation, password change, logout.

Uses an in-memory SQLite database so these tests are fully self-contained and never
touch the filesystem or a real server.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base, Player, Session
from llm.seca.auth.service import AuthService, _MAX_SESSIONS


def _make_v1_password_hash(password: str, iterations: int = 600_000) -> str:
    """Build a legacy v1 password hash for the opportunistic-rehash test.

    The v1 scheme used a raw SHA-256 digest as normalisation (vs the
    current v2 which uses 1-iter PBKDF2).  ``hashing.verify_password``
    accepts both schemes; ``needs_rehash`` flips on the scheme name and
    triggers ``login()``'s upgrade branch.  Kept local to this file
    rather than imported from hashing.py because the production module
    intentionally only emits v2 — exposing a v1 builder there would
    invite accidental regressions.
    """
    normalized = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"", 1)
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", normalized, salt, iterations)
    return (
        f"$pbkdf2-sha256${iterations}${base64.b64encode(salt).decode()}"
        f"${base64.b64encode(dk).decode()}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
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
# Register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_creates_player(self, db, service):
        player = service.register("alice@example.com", "securepass1")
        assert player.id is not None
        assert player.email == "alice@example.com"
        assert db.query(Player).filter_by(email="alice@example.com").count() == 1

    def test_register_hashes_password(self, service):
        player = service.register("bob@example.com", "mypassword")
        assert player.password_hash != "mypassword"
        assert "$pbkdf2-sha256-v2$" in player.password_hash

    def test_register_duplicate_email_raises_value_error(self, service):
        service.register("dup@example.com", "pass1234")
        with pytest.raises(ValueError, match="Registration failed"):
            service.register("dup@example.com", "anotherpass")

    def test_register_does_not_raise_http_exception(self, service):
        """Service layer must not raise HTTPException — that is the router's job."""
        from fastapi import HTTPException

        service.register("x@example.com", "pass1234")
        with pytest.raises(ValueError):
            service.register("x@example.com", "pass1234")
        # Verify it was ValueError, not HTTPException
        try:
            service.register("x@example.com", "pass1234")
        except ValueError:
            pass
        except Exception as exc:
            pytest.fail(f"Expected ValueError, got {type(exc).__name__}: {exc}")

    def test_register_short_password_raises(self, service):
        """Sprint 6.C — pin the < 8 char rejection in register().  The
        same rule is enforced at the Pydantic layer for the HTTP path
        but the service layer must also fail-fast on direct calls."""
        with pytest.raises(ValueError, match="at least 8 characters"):
            service.register("short@example.com", "abc")

    def test_register_long_password_raises(self, service):
        """Sprint 6.C — pin the > 1000 char rejection in register()."""
        with pytest.raises(ValueError, match="Password too long"):
            service.register("long@example.com", "x" * 1001)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_returns_token_and_player(self, service):
        service.register("login@example.com", "pass1234")
        token, player = service.login("login@example.com", "pass1234")
        assert isinstance(token, str)
        assert len(token) > 20
        assert player.email == "login@example.com"

    def test_login_creates_session_record(self, db, service):
        service.register("sess@example.com", "pass1234")
        service.login("sess@example.com", "pass1234")
        assert db.query(Session).count() == 1

    def test_login_stores_token_hash_not_plaintext(self, db, service):
        service.register("hash@example.com", "pass1234")
        token, _ = service.login("hash@example.com", "pass1234")
        sess = db.query(Session).first()
        assert sess.token_hash != token
        assert sess.token_hash == hashlib.sha256(token.encode()).hexdigest()

    def test_login_invalid_email_raises_value_error(self, service):
        with pytest.raises(ValueError, match="Invalid credentials"):
            service.login("nobody@example.com", "pass1234")

    def test_login_wrong_password_raises_value_error(self, service):
        service.register("wrong@example.com", "correctpass")
        with pytest.raises(ValueError, match="Invalid credentials"):
            service.login("wrong@example.com", "wrongpass")

    def test_login_device_info_stored(self, db, service):
        service.register("device@example.com", "pass1234")
        service.login("device@example.com", "pass1234", device_info="android-v1.2")
        sess = db.query(Session).first()
        assert sess.device_info == "android-v1.2"

    def test_login_with_legacy_hash_triggers_opportunistic_rehash(self, db, service):
        """Sprint 6.C — pin the H1 opportunistic-upgrade path.  A player
        whose stored hash uses the v1 scheme (raw SHA-256 normalisation)
        must have their hash rewritten to v2 on the next successful
        login.  Without this, legacy users stay on the weaker scheme
        forever; with it, the population migrates as users authenticate.
        """
        password = "legacy-pass-1"
        legacy_hash = _make_v1_password_hash(password)
        # Sanity: the legacy hash uses the v1 scheme, not v2.
        assert "$pbkdf2-sha256$" in legacy_hash
        assert "$pbkdf2-sha256-v2$" not in legacy_hash

        player = Player(
            email="legacy@example.com",
            password_hash=legacy_hash,
            player_embedding="[]",
        )
        db.add(player)
        db.commit()

        # Successful login on the legacy hash.
        token, _ = service.login("legacy@example.com", password)
        assert token is not None

        # The stored hash is now v2 — opportunistic upgrade fired.
        db.refresh(player)
        assert player.password_hash != legacy_hash
        assert "$pbkdf2-sha256-v2$" in player.password_hash

    def test_login_eleventh_session_evicts_oldest(self, db, service):
        """Sprint 6.C — pin the H3 max-sessions cap.  When a player has
        ``_MAX_SESSIONS`` (10) active sessions and logs in again, the
        oldest session is deleted so the cap is preserved.  Without
        this, a hostile or buggy client could spam logins and balloon
        the sessions table indefinitely.
        """
        service.register("manysess@example.com", "many-pass-1")
        for _ in range(_MAX_SESSIONS + 2):  # one extra to confirm steady-state
            service.login("manysess@example.com", "many-pass-1")

        player = db.query(Player).filter_by(email="manysess@example.com").first()
        assert player is not None
        active = db.query(Session).filter(Session.player_id == player.id).count()
        assert active == _MAX_SESSIONS, (
            f"max-sessions cap violated: expected {_MAX_SESSIONS}, got {active}"
        )


# ---------------------------------------------------------------------------
# Session validation
# ---------------------------------------------------------------------------


class TestGetPlayerBySession:
    def test_valid_session_returns_player(self, service):
        service.register("valid@example.com", "pass1234")
        token, _ = service.login("valid@example.com", "pass1234")

        from llm.seca.auth.tokens import decode_token

        payload = decode_token(token)
        player = service.get_player_by_session(payload["session_id"], token)
        assert player is not None
        assert player.email == "valid@example.com"

    def test_unknown_session_id_returns_none(self, service):
        result = service.get_player_by_session("nonexistent-session-id", "fake-token")
        assert result is None

    def test_wrong_token_against_real_session_returns_none(self, db, service):
        """F-07 (per-token revocation): a token whose sha256 does NOT
        match session.token_hash must fail get_player_by_session even
        if the session_id is real and unexpired.  This is the lever
        that revokes a previously-rotated JWT — router.rotate_session_token
        updates the stored hash after each successful call, so the
        just-superseded token's hash no longer matches.

        Lineage:
          Pre-PR-#66: this check existed (BUG-7 fix).
          PR-#66 → F-07: check removed because rotation didn't update
                  token_hash, breaking the second authenticated call.
          F-07 (2026-05-11): check re-introduced together with
                  rotate_session_token, which keeps the hash fresh on
                  every rotation so the rotation feature still works
                  AND old tokens are immediately revoked.
        """
        service.register("wrong_tok@example.com", "pass1234")
        token, _ = service.login("wrong_tok@example.com", "pass1234")

        from llm.seca.auth.tokens import decode_token

        payload = decode_token(token)
        # F-07: a wrong token against a real session_id is rejected.
        result = service.get_player_by_session(payload["session_id"], "completely-wrong-token")
        assert result is None, (
            "F-07 violated: wrong token validated against a real "
            "session_id.  rotate_session_token + per-request hash "
            "check together provide per-token revocation."
        )

        # The real token still validates (sanity).
        assert service.get_player_by_session(payload["session_id"], token) is not None

    def test_expired_db_session_returns_none(self, db, service):
        service.register("expired@example.com", "pass1234")
        token, _ = service.login("expired@example.com", "pass1234")

        from llm.seca.auth.tokens import decode_token

        payload = decode_token(token)
        # Force-expire the DB session
        sess = db.query(Session).filter_by(id=payload["session_id"]).first()
        sess.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()

        result = service.get_player_by_session(payload["session_id"], token)
        assert result is None


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


class TestChangePassword:
    def test_change_password_succeeds(self, service):
        player = service.register("chpw@example.com", "oldpass12")
        service.login("chpw@example.com", "oldpass12")
        service.change_password(player, "oldpass12", "newpass99")
        # Login with new password must succeed
        token, _ = service.login("chpw@example.com", "newpass99")
        assert token is not None

    def test_change_password_wrong_current_raises(self, service):
        player = service.register("wrongcur@example.com", "pass1234")
        with pytest.raises(ValueError, match="Current password is incorrect"):
            service.change_password(player, "notmypassword", "newpass99")

    def test_change_password_too_short_raises(self, service):
        player = service.register("short@example.com", "pass1234")
        with pytest.raises(ValueError, match="at least 8 characters"):
            service.change_password(player, "pass1234", "abc")

    def test_change_password_current_too_long_raises(self, service):
        """Sprint 6.C — the > 1000 char rejection on ``current_password``
        fires BEFORE the verify_password call, so an attacker can't burn
        PBKDF2 cycles by submitting a megabyte string."""
        player = service.register("longcur@example.com", "pass1234")
        with pytest.raises(ValueError, match="Password too long"):
            service.change_password(player, "x" * 1001, "newpass1")

    def test_change_password_new_too_long_raises(self, service):
        """Sprint 6.C — same 1000-char cap on ``new_password``."""
        player = service.register("longnew@example.com", "pass1234")
        with pytest.raises(ValueError, match="Password too long"):
            service.change_password(player, "pass1234", "x" * 1001)

    def test_old_password_rejected_after_change(self, service):
        player = service.register("oldpw@example.com", "firstpass")
        service.change_password(player, "firstpass", "secondpass")
        with pytest.raises(ValueError, match="Invalid credentials"):
            service.login("oldpw@example.com", "firstpass")


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_removes_session(self, db, service):
        service.register("logout@example.com", "pass1234")
        token, _ = service.login("logout@example.com", "pass1234")

        from llm.seca.auth.tokens import decode_token

        payload = decode_token(token)
        assert db.query(Session).count() == 1

        service.logout(payload["session_id"])
        assert db.query(Session).count() == 0

    def test_logout_then_session_validation_fails(self, service):
        service.register("lo2@example.com", "pass1234")
        token, _ = service.login("lo2@example.com", "pass1234")

        from llm.seca.auth.tokens import decode_token

        payload = decode_token(token)
        service.logout(payload["session_id"])

        result = service.get_player_by_session(payload["session_id"], token)
        assert result is None

    def test_logout_nonexistent_session_is_noop(self, service):
        """Logging out a session that doesn't exist must not raise."""
        service.logout("does-not-exist")
