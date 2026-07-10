"""
Backend tests for POST /feedback — user-submitted product feedback.

The endpoint persists one free-form "Send feedback" message from the
Android drawer form.  Untrusted user text: length-capped and trimmed at
the Pydantic boundary, stored verbatim, and NEVER logged (the log line
carries only the server-issued player id + message length).

Pinned invariants
-----------------
 1. FB_MESSAGE_REQUIRED_NON_BLANK    ""/whitespace message → ValidationError.
 2. FB_MESSAGE_LENGTH_BOUNDED        >2000-char message → ValidationError;
                                     exactly 2000 accepted.
 3. FB_MESSAGE_TRIMMED               surrounding whitespace stripped before storage.
 4. FB_APP_VERSION_OPTIONAL          omitted / blank app_version → stored NULL.
 5. FB_APP_VERSION_LENGTH_BOUNDED    >64-char app_version → ValidationError;
                                     exactly 64 accepted.
 6. FB_HAPPY_PATH_PERSISTS           row inserted with player_id, verbatim
                                     message, app_version, created_at.
 7. FB_RESPONSE_SHAPE                response is {status: "received", id: <uuid>}.
 8. FB_NO_DEDUP                      same message twice → two rows (feedback is
                                     not idempotent by nature).
 9. FB_UNICODE_VERBATIM              emoji / multiline / unicode text survives
                                     storage byte-for-byte (post-trim).
10. FB_MESSAGE_NEVER_LOGGED          the message body must not appear in any
                                     log record (CWE-117 posture: nothing
                                     attacker-controlled reaches the log).
11. FB_ROUTE_REGISTERED              POST /feedback is registered on server.app.
"""

from __future__ import annotations

import logging
import os
import uuid as uuid_module

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

# Import all model modules so create_all sees every table.
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401
import llm.seca.feedback.models  # noqa: F401

from llm.seca.auth.models import Base, Player
from llm.seca.feedback.models import (
    MAX_APP_VERSION_LEN,
    MAX_FEEDBACK_MESSAGE_LEN,
    FeedbackMessage,
)
from llm.seca.feedback.router import FeedbackRequest, submit_feedback
from llm.seca.shared_limiter import limiter


def _fake_request() -> StarletteRequest:
    """Minimal Request that satisfies slowapi's isinstance check.  The
    limiter is disabled inside each test so the request itself is
    never inspected for an IP / route key."""
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/feedback",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session with the full schema; torn down after each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def player(db_session):
    p = Player(
        email="feedback@test.com",
        password_hash="dummy-hash",
        rating=1500.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
        training_xp=0,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _submit(player, db, **kwargs):
    """Call the POST /feedback handler directly, bypassing FastAPI
    dependency injection.  Disables the global limiter for the duration
    of the call so unit tests don't depend on slowapi state across
    tests."""
    req = FeedbackRequest(**kwargs)
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        return submit_feedback(req=req, request=_fake_request(), player=player, db=db)
    finally:
        limiter.enabled = prev_enabled


# ---------------------------------------------------------------------------
# 1.  Schema validation (Pydantic only — no DB)
# ---------------------------------------------------------------------------


class TestFeedbackRequestValidation:
    """FeedbackRequest enforces message presence + length bounds."""

    def test_empty_message_rejected(self):
        """FB_MESSAGE_REQUIRED_NON_BLANK."""
        for blank in ("", "   ", "\t\n"):
            with pytest.raises(ValidationError, match="message must not be empty"):
                FeedbackRequest(message=blank)

    def test_message_too_long_rejected(self):
        """FB_MESSAGE_LENGTH_BOUNDED."""
        with pytest.raises(ValidationError, match="message must be at most"):
            FeedbackRequest(message="x" * (MAX_FEEDBACK_MESSAGE_LEN + 1))

    def test_message_at_limit_accepted(self):
        # Exactly MAX_FEEDBACK_MESSAGE_LEN chars must pass — boundary test.
        req = FeedbackRequest(message="x" * MAX_FEEDBACK_MESSAGE_LEN)
        assert len(req.message) == MAX_FEEDBACK_MESSAGE_LEN

    def test_message_strips_surrounding_whitespace(self):
        """FB_MESSAGE_TRIMMED."""
        req = FeedbackRequest(message="  the board froze after castling  ")
        assert req.message == "the board froze after castling"

    def test_app_version_omitted_defaults_to_none(self):
        """FB_APP_VERSION_OPTIONAL."""
        req = FeedbackRequest(message="hello")
        assert req.app_version is None

    def test_blank_app_version_normalised_to_none(self):
        """FB_APP_VERSION_OPTIONAL — ""/whitespace carries no information."""
        for blank in ("", "   ", "\t\n"):
            req = FeedbackRequest(message="hello", app_version=blank)
            assert req.app_version is None

    def test_app_version_too_long_rejected(self):
        """FB_APP_VERSION_LENGTH_BOUNDED."""
        with pytest.raises(ValidationError, match="app_version must be at most"):
            FeedbackRequest(message="hello", app_version="v" * (MAX_APP_VERSION_LEN + 1))

    def test_app_version_at_limit_accepted(self):
        req = FeedbackRequest(message="hello", app_version="v" * MAX_APP_VERSION_LEN)
        assert req.app_version is not None
        assert len(req.app_version) == MAX_APP_VERSION_LEN

    def test_app_version_strips_surrounding_whitespace(self):
        req = FeedbackRequest(message="hello", app_version="  1.4.2  ")
        assert req.app_version == "1.4.2"


# ---------------------------------------------------------------------------
# 2.  Endpoint behaviour (against an in-memory DB)
# ---------------------------------------------------------------------------


class TestFeedbackEndpoint:
    """POST /feedback behaviour with a real SQLAlchemy session."""

    def test_happy_path_persists_row(self, player, db_session):
        """FB_HAPPY_PATH_PERSISTS + FB_RESPONSE_SHAPE."""
        result = _submit(
            player,
            db_session,
            message="The coach chat overlaps the board on my phone.",
            app_version="1.4.2",
        )
        assert result.status == "received"
        # id must be a server-issued UUID — parseable, non-empty.
        assert str(uuid_module.UUID(result.id)) == result.id

        row = db_session.query(FeedbackMessage).filter(FeedbackMessage.id == result.id).one()
        assert row.player_id == player.id
        assert row.message == "The coach chat overlaps the board on my phone."
        assert row.app_version == "1.4.2"
        assert row.created_at is not None

    def test_app_version_omitted_stored_null(self, player, db_session):
        """FB_APP_VERSION_OPTIONAL — storage side."""
        result = _submit(player, db_session, message="just a note")
        row = db_session.query(FeedbackMessage).filter(FeedbackMessage.id == result.id).one()
        assert row.app_version is None

    def test_same_message_twice_inserts_two_rows(self, player, db_session):
        """FB_NO_DEDUP — feedback is not idempotent by nature."""
        first = _submit(player, db_session, message="same words")
        second = _submit(player, db_session, message="same words")
        assert first.id != second.id
        count = (
            db_session.query(FeedbackMessage)
            .filter(FeedbackMessage.player_id == player.id)
            .count()
        )
        assert count == 2

    def test_unicode_message_stored_verbatim(self, player, db_session):
        """FB_UNICODE_VERBATIM — emoji, multiline, non-Latin text."""
        text = "Первая строка 🎉\nвторая — with a dash — и ёжик"
        result = _submit(player, db_session, message=text)
        row = db_session.query(FeedbackMessage).filter(FeedbackMessage.id == result.id).one()
        assert row.message == text

    def test_message_body_never_logged(self, player, db_session, caplog):
        """FB_MESSAGE_NEVER_LOGGED — CWE-117 posture.

        The message is attacker-controlled text (may embed CR/LF); the
        router must log only the server-issued player id + the length.
        A unique marker in the message must therefore never appear in
        any captured log record.
        """
        marker = "XKCD-UNIQUE-MARKER-42\r\nFORGED LOG LINE"
        with caplog.at_level(logging.INFO, logger="llm.seca.feedback.router"):
            _submit(player, db_session, message=marker)
        assert any(
            "feedback received" in record.getMessage() for record in caplog.records
        ), "the endpoint must emit its operator-visible receipt log line"
        assert "XKCD-UNIQUE-MARKER-42" not in caplog.text
        assert "FORGED LOG LINE" not in caplog.text


# ---------------------------------------------------------------------------
# 3.  Server wiring
# ---------------------------------------------------------------------------


class TestPins:
    def test_fb_route_registered_on_server(self):
        """FB_ROUTE_REGISTERED."""
        from llm import server as server_module

        for route in server_module.app.routes:
            if getattr(route, "path", None) == "/feedback":
                assert "POST" in getattr(route, "methods", set())
                break
        else:
            pytest.fail("server.app must expose POST /feedback")
