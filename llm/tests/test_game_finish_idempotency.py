"""Tests for /game/finish idempotent replay (audit 2026-07-14, P2 #1).

The Android client re-POSTs an identical finish on Timeout (``withRetry``
x3): when the mobile network dropped the RESPONSE after the server had
already committed, the retry used to create a second GameEvent history
row and apply the rating delta twice.  The fix has three layers:

* ``_replayed_finish_response`` — handler-level dedup keyed on
  ``app_game_id``: serve the stored ``GameFinishResult`` payload (or the
  202 pending shape when it never landed) instead of re-running the
  finish.  Tested directly here.
* the guard's PLACEMENT — before the engine recompute, so a retry costs
  one SELECT, not a Stockfish batch.  Source-pinned here.
* the Postgres partial unique index on ``app_game_id`` (init_schema) +
  the ``IntegrityError`` → replay catch for the concurrent-retry race
  the SELECT can't see.  The catch is source-pinned here; the index
  itself is Postgres-only DDL (same gating as the import-jobs index).

Stable test IDs (do NOT rename): FIN_IDEM_01..07.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401

from llm.seca.auth.models import Base, Player
from llm.seca.events.models import GameEvent, GameFinishResult
from llm.seca.events.router import _finish_game_body, _replayed_finish_response

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
        email="idem@test.com",
        password_hash="dummy",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def other_player(db_session):
    p = Player(
        email="other-idem@test.com",
        password_hash="dummy",
        rating=1200.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


_PGN = '[Event "T"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'


def _event(db_session, player, app_game_id: str) -> GameEvent:
    ev = GameEvent(
        player_id=str(player.id),
        pgn=_PGN,
        result="win",
        accuracy=0.8,
        weaknesses_json="{}",
        app_game_id=app_game_id,
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    return ev


# ---------------------------------------------------------------------------
# FIN_IDEM — replay helper behaviour
# ---------------------------------------------------------------------------


class TestReplayedFinishResponse:
    def test_fin_idem_01_no_event_returns_none(self, db_session, player):
        """FIN_IDEM_01: fresh finish (no event for this game_id) proceeds."""
        assert _replayed_finish_response(db_session, player, "game-x") is None

    def test_fin_idem_02_stored_payload_is_replayed(self, db_session, player):
        """FIN_IDEM_02: the retry gets the EXACT body the original POST
        returned — no new rows, no recompute."""
        ev = _event(db_session, player, "game-1")
        stored = {"status": "stored", "new_rating": 1207.5, "event_id": str(ev.id)}
        db_session.add(
            GameFinishResult(event_id=str(ev.id), response_json=json.dumps(stored))
        )
        db_session.commit()

        replayed = _replayed_finish_response(db_session, player, "game-1")
        assert replayed == stored
        assert db_session.query(GameEvent).count() == 1

    def test_fin_idem_03_missing_result_row_serves_pending(self, db_session, player):
        """FIN_IDEM_03: event committed but the response row never landed
        (original request died in the persistence window) → the 202
        pending shape of GET /game/finish/{id}/status, NOT a re-run —
        re-running would double-apply the rating."""
        ev = _event(db_session, player, "game-2")
        replayed = _replayed_finish_response(db_session, player, "game-2")
        assert isinstance(replayed, JSONResponse)
        assert replayed.status_code == 202
        body = json.loads(replayed.body)
        assert body == {"status": "pending", "event_id": str(ev.id)}

    def test_fin_idem_04_corrupt_stored_payload_serves_pending(self, db_session, player):
        """FIN_IDEM_04: an unreadable stored payload degrades to the
        pending shape instead of 500ing the retry."""
        ev = _event(db_session, player, "game-3")
        db_session.add(GameFinishResult(event_id=str(ev.id), response_json="{not json"))
        db_session.commit()
        replayed = _replayed_finish_response(db_session, player, "game-3")
        assert isinstance(replayed, JSONResponse)
        assert replayed.status_code == 202

    def test_fin_idem_05_scoped_to_the_calling_player(
        self, db_session, player, other_player
    ):
        """FIN_IDEM_05: another player's event under the same game_id
        (cannot happen with server-minted ids; defense-in-depth) does
        not satisfy the caller's replay."""
        _event(db_session, other_player, "game-4")
        assert _replayed_finish_response(db_session, player, "game-4") is None

    def test_fin_idem_06_oldest_event_wins(self, db_session, player):
        """FIN_IDEM_06: pre-index legacy duplicates — the FIRST stored
        event (the one whose side effects actually applied first) is the
        replay source."""
        first = _event(db_session, player, "game-5")
        second = _event(db_session, player, "game-5")  # legacy dup
        db_session.add(
            GameFinishResult(
                event_id=str(first.id), response_json=json.dumps({"status": "stored"})
            )
        )
        db_session.add(
            GameFinishResult(
                event_id=str(second.id),
                response_json=json.dumps({"status": "stored-second"}),
            )
        )
        db_session.commit()
        replayed = _replayed_finish_response(db_session, player, "game-5")
        assert replayed == {"status": "stored"}


# ---------------------------------------------------------------------------
# FIN_IDEM_07 — guard placement + race-catch source pins
# ---------------------------------------------------------------------------


class TestGuardWiring:
    def test_fin_idem_07_guard_precedes_recompute_and_race_is_caught(self):
        """FIN_IDEM_07: (a) the dedup guard must run BEFORE the engine
        recompute (a retry costs one SELECT, not a ~40-acquire Stockfish
        batch), and (b) the store_game call must catch IntegrityError
        and fall back to the replay path (the concurrent-retry race the
        SELECT cannot see; enforced by the Postgres partial unique index
        on app_game_id).  Source-level pins: both properties are
        ordering/wiring facts a behavioural SQLite test cannot observe."""
        src = inspect.getsource(_finish_game_body)
        guard_pos = src.index("_replayed_finish_response")
        recompute_pos = src.index("_resolve_authoritative_accuracy")
        assert guard_pos < recompute_pos, (
            "the idempotent-replay guard must precede the engine recompute"
        )
        assert "except IntegrityError" in src, (
            "store_game must catch the unique-index race and serve the "
            "winner's row"
        )
