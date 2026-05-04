"""
Backend tests for PATCH /auth/me — partial profile update.

Used by the Android Onboarding screen to forward the calibration
estimate (rating + confidence) to the server so the adaptation layer
can dispatch a first opponent at the right level.

Pinned invariants
-----------------
 1. PATCH_REQUIRES_AT_LEAST_ONE_FIELD   empty body → 400.
 2. PATCH_RATING_BOUNDS                 rating outside (0, 4000] → ValidationError.
 3. PATCH_CONFIDENCE_BOUNDS             confidence outside [0, 1] → ValidationError.
 4. PATCH_PARTIAL_RATING_ONLY           omitting confidence preserves the existing value.
 5. PATCH_PARTIAL_CONFIDENCE_ONLY       omitting rating preserves the existing value.
 6. PATCH_BOTH_FIELDS_UPDATED           both fields update simultaneously.
 7. PATCH_RESPONSE_SHAPE                response matches GET /auth/me.
 8. PATCH_PERSISTS_TO_DB                refresh from DB returns the new values.
 9. PATCH_DOES_NOT_TOUCH_EMAIL_OR_ID    email + id round-trip unchanged.
 10. PATCH_DOES_NOT_TOUCH_SKILL_VECTOR   skill_vector_json is preserved.
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
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

from llm.seca.auth.models import Base, Player
from llm.seca.auth.router import UpdateMeRequest, me, update_me
from llm.seca.shared_limiter import limiter


def _fake_request() -> StarletteRequest:
    """Minimum starlette.requests.Request that satisfies slowapi's
    isinstance check on the @limiter.limit decorator.  We disable the
    limiter inside each test so the request itself is never inspected
    for an IP / route key."""
    return StarletteRequest({
        "type": "http", "method": "PATCH", "path": "/auth/me",
        "headers": [], "client": ("127.0.0.1", 0),
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session with the auth schema; torn down after each test."""
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
        email="patch@test.com",
        password_hash="dummy-hash",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json='{"tactics": 0.6}',
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _patch(player, db, **kwargs):
    """Call the PATCH /auth/me handler directly, bypassing FastAPI
    dependency injection.  Disables the global limiter for the
    duration of the call so unit tests don't depend on
    slowapi state across tests."""
    req = UpdateMeRequest(**kwargs)
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        return update_me(req=req, request=_fake_request(), player=player, db=db)
    finally:
        limiter.enabled = prev_enabled


# ---------------------------------------------------------------------------
# 1.  Schema validation (Pydantic only — no DB)
# ---------------------------------------------------------------------------


class TestUpdateMeRequestValidation:
    """UpdateMeRequest schema enforces bounds and field optionality."""

    def test_both_fields_optional(self):
        """Constructing with no fields succeeds at the schema level — the
        endpoint then rejects the empty payload with a 400 (see
        TestUpdateMeEndpoint.test_empty_body_returns_400)."""
        req = UpdateMeRequest()
        assert req.rating is None
        assert req.confidence is None

    def test_rating_only(self):
        req = UpdateMeRequest(rating=1500.0)
        assert req.rating == 1500.0
        assert req.confidence is None

    def test_confidence_only(self):
        req = UpdateMeRequest(confidence=0.75)
        assert req.rating is None
        assert req.confidence == 0.75

    @pytest.mark.parametrize("rating", [0.0, -1.0, -100.0, 4001.0, 5000.0, 1e9])
    def test_rating_out_of_bounds_raises(self, rating):
        with pytest.raises(ValidationError, match="rating must be in"):
            UpdateMeRequest(rating=rating)

    @pytest.mark.parametrize("rating", [0.1, 800.0, 1500.0, 2600.0, 3700.0, 4000.0])
    def test_rating_within_bounds_accepted(self, rating):
        req = UpdateMeRequest(rating=rating)
        assert req.rating == rating

    @pytest.mark.parametrize("confidence", [-0.01, -1.0, 1.01, 1.5, 100.0])
    def test_confidence_out_of_bounds_raises(self, confidence):
        with pytest.raises(ValidationError, match="confidence must be in"):
            UpdateMeRequest(confidence=confidence)

    @pytest.mark.parametrize("confidence", [0.0, 0.25, 0.5, 0.85, 1.0])
    def test_confidence_within_bounds_accepted(self, confidence):
        req = UpdateMeRequest(confidence=confidence)
        assert req.confidence == confidence


# ---------------------------------------------------------------------------
# 2.  Endpoint behaviour (against an in-memory DB)
# ---------------------------------------------------------------------------


class TestUpdateMeEndpoint:
    """PATCH /auth/me behaviour with a real SQLAlchemy session."""

    def test_empty_body_returns_400(self, player, db_session):
        """PATCH_REQUIRES_AT_LEAST_ONE_FIELD."""
        with pytest.raises(HTTPException) as exc:
            _patch(player, db_session)
        assert exc.value.status_code == 400
        assert "no fields" in exc.value.detail.lower()

    def test_rating_only_preserves_confidence(self, player, db_session):
        """PATCH_PARTIAL_RATING_ONLY."""
        original_confidence = player.confidence
        result = _patch(player, db_session, rating=1720.0)
        assert result["rating"] == 1720.0
        assert result["confidence"] == original_confidence

    def test_confidence_only_preserves_rating(self, player, db_session):
        """PATCH_PARTIAL_CONFIDENCE_ONLY."""
        original_rating = player.rating
        result = _patch(player, db_session, confidence=0.85)
        assert result["confidence"] == 0.85
        assert result["rating"] == original_rating

    def test_both_fields_updated(self, player, db_session):
        """PATCH_BOTH_FIELDS_UPDATED."""
        result = _patch(player, db_session, rating=1900.0, confidence=0.85)
        assert result["rating"] == 1900.0
        assert result["confidence"] == 0.85

    def test_response_shape_matches_get_me(self, player, db_session):
        """PATCH_RESPONSE_SHAPE — same fields as GET /auth/me so the
        Android client can replace its cache from a single round-trip."""
        patch_result = _patch(player, db_session, rating=1500.0)
        get_result = me(player=player)
        assert set(patch_result.keys()) == set(get_result.keys())

    def test_persists_to_db(self, player, db_session):
        """PATCH_PERSISTS_TO_DB — re-fetching the player from a fresh
        query returns the patched values, not the pre-patch defaults."""
        _patch(player, db_session, rating=2100.0, confidence=0.25)
        db_session.expire_all()
        refetched = db_session.query(Player).filter_by(id=player.id).first()
        assert refetched.rating == 2100.0
        assert refetched.confidence == 0.25

    def test_does_not_touch_email_or_id(self, player, db_session):
        """PATCH_DOES_NOT_TOUCH_EMAIL_OR_ID — partial update must not
        clobber identity fields."""
        original_id = player.id
        original_email = player.email
        result = _patch(player, db_session, rating=1500.0)
        assert result["id"] == original_id
        assert result["email"] == original_email

    def test_does_not_touch_skill_vector(self, player, db_session):
        """PATCH_DOES_NOT_TOUCH_SKILL_VECTOR — onboarding does not
        produce a skill_vector, so PATCH must leave it untouched
        (otherwise weakness tags in MainActivity would reset on every
        calibration update)."""
        result = _patch(player, db_session, rating=1500.0, confidence=0.5)
        assert result["skill_vector"] == {"tactics": 0.6}


# ---------------------------------------------------------------------------
# 3.  X-HTTP-Method-Override middleware (server.py)
# ---------------------------------------------------------------------------


class TestHttpMethodOverrideMiddleware:
    """The Android client sends POST + ``X-HTTP-Method-Override: PATCH``
    because the JDK's HttpURLConnection rejects PATCH as a request
    method on JDK 17.  The middleware in server.py promotes the request
    to a real PATCH so the route handler runs.  These tests pin the
    behaviour at the middleware layer so a future refactor can't
    silently break the override."""

    def test_post_with_patch_override_routes_to_patch_handler(self):
        """POST + X-HTTP-Method-Override: PATCH is dispatched as PATCH."""
        from fastapi.testclient import TestClient

        from llm.server import app

        # We don't need real auth here — just confirm the middleware
        # promotes the method.  401 is fine: it means routing reached
        # the PATCH handler (which then required Bearer auth).  If the
        # middleware were broken, FastAPI would return 405 (Method Not
        # Allowed) because there's no POST /auth/me route.
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/auth/me",
            json={"rating": 1500.0},
            headers={"X-HTTP-Method-Override": "PATCH"},
        )
        assert response.status_code != 405, (
            "POST + override must route to PATCH /auth/me; got 405 means "
            "the middleware did NOT promote the method"
        )

    def test_post_without_override_returns_405(self):
        """Plain POST /auth/me (no override header) is still 405 — the
        middleware must be opt-in via the header, not a silent rewriter
        of every POST."""
        from fastapi.testclient import TestClient

        from llm.server import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/auth/me", json={"rating": 1500.0})
        assert response.status_code == 405

    def test_override_is_case_insensitive(self):
        """Header value should accept "patch" (lowercase) — common
        convention for HTTP headers, and the middleware does
        ``.upper()`` to normalise."""
        from fastapi.testclient import TestClient

        from llm.server import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/auth/me",
            json={"rating": 1500.0},
            headers={"X-HTTP-Method-Override": "patch"},
        )
        assert response.status_code != 405

    def test_override_to_disallowed_method_is_ignored(self):
        """Only allow-listed methods are promoted.  A POST with
        ``X-HTTP-Method-Override: DELETE`` must NOT be dispatched as
        DELETE — otherwise the override could be used to bypass
        intentional method restrictions on other routes."""
        from fastapi.testclient import TestClient

        from llm.server import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/auth/me",
            json={"rating": 1500.0},
            headers={"X-HTTP-Method-Override": "DELETE"},
        )
        # No POST /auth/me route exists, so the unpromoted POST yields 405.
        assert response.status_code == 405
