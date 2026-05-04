"""
Backend tests for the sliding-session behaviour on
[AuthService.get_player_by_session].

Background
----------
Pre-2026-04 the JWT exp was 15 minutes and there was no refresh
path, so users got bounced to login every 15 min of activity.
ACCESS_EXPIRE_MINUTES is now 7 days (matching the session window),
and AuthService bumps session.expires_at on each successful
validation when the session has < 1 day remaining — together that
gives an active user 7 days of *idleness* before re-login.

Pinned invariants
-----------------
 1. SLIDE_VALID_AUTH_EXTENDS    a successful validation extends the
                                 session's expires_at when it's near
                                 the threshold.
 2. SLIDE_FRESH_SESSION_NOT_BUMPED a session with > 1 day remaining
                                 is NOT re-written on every call
                                 (avoids per-request DB churn).
 3. SLIDE_FAILED_AUTH_NO_BUMP    bad token / wrong session_id never
                                 extend expires_at (defence against
                                 keep-alive on a revoked session).
 4. SLIDE_EXPIRED_SESSION_NOT_REVIVED an already-expired session
                                 cannot be slid forward — get_player
                                 returns None first.
 5. SLIDE_PERSISTED_TO_DB        the new expires_at survives a
                                 db.expire_all + re-fetch.
 6. JWT_EXP_NOT_15_MINUTES       safety pin: the JWT exp constant is
                                 NOT the old 15-min default.  A
                                 future "let's tighten security"
                                 refactor that drops it back to 15
                                 must explicitly update this test.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.auth.models import Base, Player, Session
from llm.seca.auth.service import (
    AuthService,
    _SESSION_EXTEND,
    _SESSION_SLIDE_THRESHOLD,
)
from llm.seca.auth.tokens import ACCESS_EXPIRE_MINUTES


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


def _login_and_get_session(service, db, email="slide@test.com", password="slide-pass-1"):
    """Register + log in, returning (token, session_row)."""
    service.register(email, password)
    token, _ = service.login(email, password)
    session_id = _decode_session_id(token)
    session_row = db.query(Session).filter_by(id=session_id).first()
    assert session_row is not None, "login must produce a session row"
    return token, session_row


def _decode_session_id(token: str) -> str:
    from llm.seca.auth.tokens import decode_token
    return decode_token(token)["session_id"]


# ---------------------------------------------------------------------------
# 1.  JWT expiry constant safety pin
# ---------------------------------------------------------------------------


def test_jwt_exp_not_15_minutes_anymore():
    """JWT_EXP_NOT_15_MINUTES — pre-sliding the JWT was 15 min and
    bounced active users.  A future refactor that drops it back to
    15 without restoring some other refresh mechanism would silently
    re-introduce the bounce; this test forces the change to be
    explicit."""
    assert ACCESS_EXPIRE_MINUTES > 15, (
        "ACCESS_EXPIRE_MINUTES must be > 15: a 15-min JWT with no "
        "refresh path bounces active users every 15 min.  See "
        "test_auth_sliding_session.py for the rationale."
    )
    # Should be days-scale, not minutes / hours.
    assert ACCESS_EXPIRE_MINUTES >= 60 * 24, (
        "ACCESS_EXPIRE_MINUTES is below 1 day; without a refresh "
        "endpoint this means daily users get bounced more than once "
        "per day.  Either bump this to ≥ 24h or add a refresh path."
    )


# ---------------------------------------------------------------------------
# 2.  Sliding-window behaviour
# ---------------------------------------------------------------------------


class TestSessionSliding:
    def test_fresh_session_not_bumped(self, service, db):
        """SLIDE_FRESH_SESSION_NOT_BUMPED — a session with > 1 day
        remaining (i.e. just-logged-in) must NOT have its expires_at
        re-written on every API call."""
        token, session_row = _login_and_get_session(service, db)
        original_expiry = session_row.expires_at

        # Hit get_player_by_session like a single API call would.
        player = service.get_player_by_session(session_row.id, token)
        assert player is not None

        # Re-fetch from DB to be certain the row wasn't touched.
        db.expire_all()
        refetched = db.query(Session).filter_by(id=session_row.id).first()
        assert refetched.expires_at == original_expiry, (
            "fresh session (> 1 day remaining) was unexpectedly bumped"
        )

    def test_near_expiry_session_extended(self, service, db):
        """SLIDE_VALID_AUTH_EXTENDS — a session inside the slide
        threshold (< 1 day remaining) gets its expires_at pushed
        forward by _SESSION_EXTEND."""
        token, session_row = _login_and_get_session(service, db)

        # Manually push expires_at into the slide-threshold window.
        # 1 hour remaining — well inside _SESSION_SLIDE_THRESHOLD.
        soon = datetime.utcnow() + timedelta(hours=1)
        session_row.expires_at = soon
        db.commit()

        before = datetime.utcnow()
        player = service.get_player_by_session(session_row.id, token)
        after = datetime.utcnow()
        assert player is not None

        db.expire_all()
        refetched = db.query(Session).filter_by(id=session_row.id).first()
        # New expiry should be roughly now + _SESSION_EXTEND.
        expected_low  = before + _SESSION_EXTEND - timedelta(seconds=2)
        expected_high = after  + _SESSION_EXTEND + timedelta(seconds=2)
        assert expected_low <= refetched.expires_at <= expected_high, (
            f"expected sliding bump to land in [{expected_low}, {expected_high}], "
            f"got {refetched.expires_at}"
        )

    def test_extension_persists_across_db_expire(self, service, db):
        """SLIDE_PERSISTED_TO_DB — the bump must commit, not just live
        in the in-memory ORM object."""
        token, session_row = _login_and_get_session(service, db)
        session_row.expires_at = datetime.utcnow() + timedelta(hours=2)
        db.commit()

        service.get_player_by_session(session_row.id, token)

        db.expire_all()
        # If commit was missing, the slid value would vanish on
        # expire_all() and the row would still show ~2h remaining.
        refetched = db.query(Session).filter_by(id=session_row.id).first()
        time_left = refetched.expires_at - datetime.utcnow()
        assert time_left > timedelta(days=6), (
            f"sliding bump didn't persist; only {time_left} remains"
        )

    def test_bad_token_does_not_slide(self, service, db):
        """SLIDE_FAILED_AUTH_NO_BUMP — sliding must happen AFTER the
        token comparison succeeds, otherwise an attacker probing with
        a stolen-then-revoked token could keep a dead session alive."""
        token, session_row = _login_and_get_session(service, db)
        session_row.expires_at = datetime.utcnow() + timedelta(hours=1)
        db.commit()
        original_expiry = session_row.expires_at

        # Wrong token — same session_id, garbage token.
        result = service.get_player_by_session(session_row.id, "not-the-real-token")
        assert result is None

        db.expire_all()
        refetched = db.query(Session).filter_by(id=session_row.id).first()
        assert refetched.expires_at == original_expiry, (
            "session was extended despite a failed token check — "
            "this would let an attacker keep a dead session alive"
        )

    def test_unknown_session_id_returns_none(self, service, db):
        # Defence against a session_id that simply doesn't exist
        # (logged out, or never created).  Must return None, not
        # silently slide some other row.
        result = service.get_player_by_session("does-not-exist", "any-token")
        assert result is None

    def test_expired_session_not_revived(self, service, db):
        """SLIDE_EXPIRED_SESSION_NOT_REVIVED — once a session is past
        its expires_at, get_player must return None.  Sliding is not
        a resurrection mechanism."""
        token, session_row = _login_and_get_session(service, db)
        session_row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()

        result = service.get_player_by_session(session_row.id, token)
        assert result is None

        # And the row stays expired — sliding only ever moves expiry
        # FORWARD from now, but here the early-return prevents the
        # bump entirely.
        db.expire_all()
        refetched = db.query(Session).filter_by(id=session_row.id).first()
        assert refetched.expires_at < datetime.utcnow(), (
            "expired session was unexpectedly slid forward"
        )

    def test_threshold_constant_is_subset_of_extension(self):
        """Defence against a misconfiguration that would make the
        threshold larger than the extension — every call would slide,
        defeating the per-call DB-write avoidance."""
        assert _SESSION_SLIDE_THRESHOLD < _SESSION_EXTEND, (
            "_SESSION_SLIDE_THRESHOLD must be smaller than _SESSION_EXTEND "
            "or the slide gating fires every call (defeats the purpose)"
        )
