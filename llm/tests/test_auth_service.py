"""Unit tests for AuthService: register, login, session validation, password change, logout.

Uses an in-memory SQLite database so these tests are fully self-contained and never
touch the filesystem or a real server.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base, Player, Session
from llm.seca.auth.service import AuthService


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

    def test_wrong_token_returns_none(self, db, service):
        service.register("wrong_tok@example.com", "pass1234")
        token, _ = service.login("wrong_tok@example.com", "pass1234")

        from llm.seca.auth.tokens import decode_token

        payload = decode_token(token)
        result = service.get_player_by_session(payload["session_id"], "completely-wrong-token")
        assert result is None

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
