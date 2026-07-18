"""
Backend tests for POST /coach/report — the in-app report affordance for
AI-generated coach content (Google Play AI-Generated Content policy,
docs/API_CONTRACTS.md §45).

Erasure/export coverage is handled by the discovery tripwires
(content_reports is in both seed universes); this file pins the endpoint.

Pinned invariants
-----------------
 1. CR_STORES_REPORT     valid report → 200 {status:received,id}; a row is
                         persisted with content / surface / fen / reason and
                         reviewed = 0 (operator queue).
 2. CR_EMPTY_REJECTED    blank content → 422, no row.
 3. CR_BAD_SURFACE       surface outside the closed vocabulary → 422.
 4. CR_TOO_LONG          content over the cap → 422.
 5. CR_REASON_OPTIONAL   omitting fen + reason still stores a report.
 6. CR_CONTENT_NOT_LOGGED the log line carries player id + surface + length,
                          never the reported content (CWE-117 / no PII).
"""

from __future__ import annotations

import logging
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from llm.seca.auth.models import Base, Player
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.moderation.models import MAX_REPORT_CONTENT_LEN, ContentReport
from llm.seca.moderation.router import router as moderation_router
from llm.seca.shared_limiter import limiter

_PID = "player-reporter"


@pytest.fixture()
def SessionLocal():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    maker = sessionmaker(bind=engine)
    db = maker()
    db.add(Player(id=_PID, email="reporter@test", password_hash="x"))
    db.commit()
    db.close()
    return maker


@pytest.fixture()
def client(SessionLocal):
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(moderation_router)

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _override_player():
        db = SessionLocal()
        try:
            return db.query(Player).filter_by(id=_PID).one()
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_player] = _override_player

    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield TestClient(app)
    finally:
        limiter.enabled = prev


def _count(SessionLocal) -> int:
    db = SessionLocal()
    try:
        return db.query(ContentReport).count()
    finally:
        db.close()


def test_stores_report(client, SessionLocal):
    """CR_STORES_REPORT."""
    r = client.post(
        "/coach/report",
        json={
            "content": "  an offensive coach reply  ",
            "surface": "chat",
            "fen": "8/8/8/8/8/8/8/K6k w - - 0 1",
            "reason": "this is inappropriate",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "received"
    assert body["id"]

    db = SessionLocal()
    try:
        row = db.query(ContentReport).one()
        assert row.player_id == _PID
        assert row.content == "an offensive coach reply"  # trimmed
        assert row.surface == "chat"
        assert row.fen == "8/8/8/8/8/8/8/K6k w - - 0 1"
        assert row.reason == "this is inappropriate"
        assert row.reviewed == 0  # lands in the operator queue
    finally:
        db.close()


def test_empty_content_rejected(client, SessionLocal):
    """CR_EMPTY_REJECTED."""
    r = client.post("/coach/report", json={"content": "   ", "surface": "chat"})
    assert r.status_code == 422
    assert _count(SessionLocal) == 0


def test_bad_surface_rejected(client, SessionLocal):
    """CR_BAD_SURFACE."""
    r = client.post("/coach/report", json={"content": "x", "surface": "nonsense"})
    assert r.status_code == 422
    assert _count(SessionLocal) == 0


def test_over_long_content_rejected(client, SessionLocal):
    """CR_TOO_LONG."""
    r = client.post(
        "/coach/report",
        json={"content": "a" * (MAX_REPORT_CONTENT_LEN + 1), "surface": "chat"},
    )
    assert r.status_code == 422
    assert _count(SessionLocal) == 0


def test_reason_and_fen_optional(client, SessionLocal):
    """CR_REASON_OPTIONAL."""
    r = client.post("/coach/report", json={"content": "bad reply", "surface": "review"})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        row = db.query(ContentReport).one()
        assert row.reason is None
        assert row.fen is None
        assert row.surface == "review"
    finally:
        db.close()


def test_content_is_not_logged(client, caplog):
    """CR_CONTENT_NOT_LOGGED: player id + surface + length reach the log,
    the reported text never does."""
    secret = "UNIQUE-OFFENSIVE-PHRASE-42"
    with caplog.at_level(logging.INFO, logger="llm.seca.moderation.router"):
        r = client.post("/coach/report", json={"content": secret, "surface": "chat"})
    assert r.status_code == 200
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert _PID in joined
    assert "surface=chat" in joined
    assert secret not in joined
