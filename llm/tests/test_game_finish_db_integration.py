"""
DB integration test for /game/finish via EventStorage.

Uses an in-memory SQLite database (SQLAlchemy) — no external services required.
Verifies that store_game() actually persists a GameEvent row with the correct
fields and that the row is queryable after commit.

Pinned invariants
-----------------
 1. ROW_CREATED:         store_game() inserts exactly one GameEvent row.
 2. PGN_PERSISTED:       GameEvent.pgn matches the submitted PGN.
 3. RESULT_PERSISTED:    GameEvent.result matches the submitted result.
 4. ACCURACY_PERSISTED:  GameEvent.accuracy matches the submitted accuracy.
 5. WEAKNESSES_JSON:     weaknesses_json is a valid JSON dict with correct content.
 6. PLAYER_ID_PERSISTED: GameEvent.player_id matches the player id.
 7. ID_NONNULL:          GameEvent.id is set after commit.
 8. MULTIPLE_EVENTS:     Two store_game() calls create two separate rows.
 9. ANALYTICS_LOGGED:    AnalyticsLogger.log() is called (not suppressed silently).
10. EMPTY_WEAKNESSES:    store_game() with empty weaknesses dict stores "{}".
11. APP_GAME_ID_PERSISTED: store_game(app_game_id=...) records the in-app
                          games.id linking the row to its chat thread.
12. APP_GAME_ID_DEFAULT:  omitting app_game_id leaves the column NULL
                          (legacy / imported / pre-game_id finishes).
13. GAME_HISTORY_GAME_ID: GET /game/history projects app_game_id under the
                          ``game_id`` key (null, never a missing field).
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import all models so metadata is complete before create_all.
from llm.seca.auth.models import Base
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401

from llm.seca.events.models import GameEvent
from llm.seca.events.storage import EventStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session — torn down after each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2025.01.01"]\n'
    '[Round "1"]\n'
    '[White "Player1"]\n'
    '[Black "Player2"]\n'
    '[Result "1-0"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0"
)


# ---------------------------------------------------------------------------
# 1. ROW_CREATED
# ---------------------------------------------------------------------------


def test_store_game_inserts_one_row(db_session):
    """store_game() inserts exactly one GameEvent row."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.8,
        weaknesses={},
    )
    rows = db_session.query(GameEvent).all()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"


# ---------------------------------------------------------------------------
# 2. PGN_PERSISTED
# ---------------------------------------------------------------------------


def test_pgn_persisted_correctly(db_session):
    """GameEvent.pgn matches the submitted PGN."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.8,
        weaknesses={},
    )
    event = db_session.query(GameEvent).one()
    assert event.pgn == _VALID_PGN


# ---------------------------------------------------------------------------
# 3. RESULT_PERSISTED
# ---------------------------------------------------------------------------


def test_result_persisted_correctly(db_session):
    """GameEvent.result matches the submitted result."""
    for result_str in ("win", "loss", "draw"):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        sess = Session()
        try:
            storage = EventStorage(sess)
            storage.store_game(
                player_id="p1",
                pgn=_VALID_PGN,
                result=result_str,
                accuracy=0.5,
                weaknesses={},
            )
            event = sess.query(GameEvent).one()
            assert event.result == result_str, (
                f"Expected result '{result_str}', got '{event.result}'"
            )
        finally:
            sess.close()


# ---------------------------------------------------------------------------
# 4. ACCURACY_PERSISTED
# ---------------------------------------------------------------------------


def test_accuracy_persisted_correctly(db_session):
    """GameEvent.accuracy matches the submitted accuracy."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="draw",
        accuracy=0.72,
        weaknesses={},
    )
    event = db_session.query(GameEvent).one()
    assert abs(event.accuracy - 0.72) < 1e-5, f"accuracy mismatch: {event.accuracy}"


# ---------------------------------------------------------------------------
# 5. WEAKNESSES_JSON
# ---------------------------------------------------------------------------


def test_weaknesses_json_persisted_correctly(db_session):
    """weaknesses_json is a valid JSON dict with correct content."""
    storage = EventStorage(db_session)
    weaknesses = {"tactics": 0.6, "endgame": 0.3}
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="loss",
        accuracy=0.4,
        weaknesses=weaknesses,
    )
    event = db_session.query(GameEvent).one()
    parsed = json.loads(event.weaknesses_json)
    assert parsed == weaknesses, f"weaknesses mismatch: {parsed}"


# ---------------------------------------------------------------------------
# 6. PLAYER_ID_PERSISTED
# ---------------------------------------------------------------------------


def test_player_id_persisted_correctly(db_session):
    """GameEvent.player_id matches the player id."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="player-xyz",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.9,
        weaknesses={},
    )
    event = db_session.query(GameEvent).one()
    assert event.player_id == "player-xyz"


# ---------------------------------------------------------------------------
# 7. ID_NONNULL
# ---------------------------------------------------------------------------


def test_event_id_assigned_after_commit(db_session):
    """GameEvent.id is set (non-null, non-empty) after store_game() commits."""
    storage = EventStorage(db_session)
    event = storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.5,
        weaknesses={},
    )
    assert event.id, "GameEvent.id must be set after commit"


# ---------------------------------------------------------------------------
# 8. MULTIPLE_EVENTS
# ---------------------------------------------------------------------------


def test_two_store_game_calls_create_two_rows(db_session):
    """Two store_game() calls create two separate rows."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.8,
        weaknesses={},
    )
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="loss",
        accuracy=0.4,
        weaknesses={"tactics": 0.7},
    )
    rows = db_session.query(GameEvent).all()
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    results = {r.result for r in rows}
    assert results == {"win", "loss"}


# ---------------------------------------------------------------------------
# 9. ANALYTICS_LOGGED
# ---------------------------------------------------------------------------


def test_analytics_logger_called_on_store(db_session, monkeypatch):
    """AnalyticsLogger.log() is called — not suppressed silently."""
    from llm.seca.analytics.logger import AnalyticsLogger

    logged = []

    def fake_log(self, event_type, player_id, payload):
        logged.append({"event_type": event_type, "player_id": player_id})

    monkeypatch.setattr(AnalyticsLogger, "log", fake_log)

    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.8,
        weaknesses={},
    )
    assert len(logged) == 1, (
        "AnalyticsLogger.log() must be called exactly once per store_game() call"
    )
    assert logged[0]["player_id"] == "p1"


# ---------------------------------------------------------------------------
# 10. EMPTY_WEAKNESSES
# ---------------------------------------------------------------------------


def test_empty_weaknesses_stored_as_empty_json(db_session):
    """store_game() with empty weaknesses dict stores '{}'."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="draw",
        accuracy=0.5,
        weaknesses={},
    )
    event = db_session.query(GameEvent).one()
    parsed = json.loads(event.weaknesses_json)
    assert parsed == {}, f"Expected empty dict, got: {parsed}"


# ---------------------------------------------------------------------------
# 11. APP_GAME_ID_PERSISTED
# ---------------------------------------------------------------------------


def test_app_game_id_persisted_when_provided(db_session):
    """store_game(app_game_id=...) records the in-app games.id so the
    game-history UI can link a finished game to its coaching chat thread
    (GET /chat/history?game_id=...)."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.8,
        weaknesses={},
        app_game_id="live-game-1",
    )
    event = db_session.query(GameEvent).one()
    assert event.app_game_id == "live-game-1"


# ---------------------------------------------------------------------------
# 12. APP_GAME_ID_DEFAULT
# ---------------------------------------------------------------------------


def test_app_game_id_defaults_to_none_when_omitted(db_session):
    """Omitting app_game_id leaves the column NULL — legacy rows, Lichess
    imports, and finishes from pre-game_id clients have no chat to surface."""
    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="loss",
        accuracy=0.3,
        weaknesses={},
    )
    event = db_session.query(GameEvent).one()
    assert event.app_game_id is None


# ---------------------------------------------------------------------------
# 13. GAME_HISTORY_GAME_ID
# ---------------------------------------------------------------------------


def test_game_history_projects_app_game_id_as_game_id(db_session):
    """GET /game/history surfaces each row's app_game_id under the
    ``game_id`` key so the client can fetch that game's chat.  Rows without
    one (legacy / imported) project null — never a missing field, so the
    client's optional decode stays total."""
    from types import SimpleNamespace

    from llm.seca.events.router import game_history

    storage = EventStorage(db_session)
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.8,
        weaknesses={},
        app_game_id="live-game-1",
    )
    storage.store_game(
        player_id="p1",
        pgn=_VALID_PGN,
        result="loss",
        accuracy=0.3,
        weaknesses={},  # no app_game_id -> NULL
    )

    resp = game_history(player=SimpleNamespace(id="p1"), db=db_session)

    games = resp["games"]
    assert len(games) == 2
    # Every row carries the key (None when absent), never a missing field.
    assert all("game_id" in g for g in games), f"missing game_id key: {games}"
    assert {g["game_id"] for g in games} == {"live-game-1", None}
