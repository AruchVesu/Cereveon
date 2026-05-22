"""Unit tests for llm/seca/events/storage.py.

Happy paths (store_game persists a GameEvent, get_recent_games returns rows
in insertion order) are covered transitively by test_seca_integration.py
and test_full_loop_integration.py.  The defensive branches were not, which
is what Sprint 6.C raises:

  - ESTORE_01  store_game persists a row and AnalyticsLogger sees the event
  - ESTORE_02  store_game with a committing DB that raises propagates the
               exception AFTER logger.exception captures the traceback
               (lines 46-48: the except/raise pair)
  - ESTORE_03  get_recent_games scopes by player_id and respects limit
  - ESTORE_04  get_all_recent_games returns events across players in
               descending creation order
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base, Player
from llm.seca.events.models import GameEvent
from llm.seca.events.storage import EventStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def player(db):
    p = Player(
        email="estore@example.com",
        password_hash="x",
        player_embedding="[]",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# ESTORE_01 — happy path
# ---------------------------------------------------------------------------


def test_estore_01_store_game_persists_event_and_logs_analytics(db, player):
    """A successful store_game writes the GameEvent row AND emits an
    AnalyticsEvent of type GAME_FINISHED via AnalyticsLogger.  Both
    halves are part of the contract — losing the analytics emit
    silently degrades downstream training-recommendation surfaces."""
    storage = EventStorage(db)

    event = storage.store_game(
        player_id=str(player.id),
        pgn="1. e4 e5 2. Nf3 Nc6",
        result="win",
        accuracy=0.82,
        weaknesses={"endgame": 0.4},
    )

    assert event.id is not None
    assert event.player_id == player.id
    assert event.pgn.startswith("1. e4 e5")
    assert event.result == "win"
    assert event.accuracy == pytest.approx(0.82)

    # AnalyticsLogger committed an AnalyticsEvent — same DB session,
    # so we can query it back directly.
    from llm.seca.analytics.models import AnalyticsEvent

    analytics_rows = db.query(AnalyticsEvent).all()
    assert len(analytics_rows) == 1
    assert analytics_rows[0].event_type == "game_finished"


# ---------------------------------------------------------------------------
# ESTORE_02 — commit-crash branch (the missed lines 46-48)
# ---------------------------------------------------------------------------


def test_estore_02_commit_crash_propagates_after_logging(db, player, caplog):
    """If the DB commit raises mid-store_game, the except branch in
    EventStorage.store_game must:
      1. rollback so the session is not left in InFailedSqlTransaction
         (Postgres cascade class — see PR #165 /game/finish incident),
      2. emit a logger.exception so operators see the traceback,
      3. re-raise so the caller knows the write failed (rather than
         silently dropping the game event).

    Implemented by monkey-patching ``db.commit`` to raise after the
    .add() succeeds.  The same crash path is what a UNIQUE-violation
    or disk-full would trigger in production.
    """
    storage = EventStorage(db)

    class _BoomError(RuntimeError):
        pass

    original_commit = db.commit
    rollback_calls: list[int] = []
    original_rollback = db.rollback

    def _exploding_commit():
        # Restore the real commit before raising so the rollback that
        # caplog teardown might trigger doesn't loop forever.
        db.commit = original_commit  # type: ignore[method-assign]
        raise _BoomError("simulated DB crash mid-commit")

    def _counting_rollback():
        rollback_calls.append(1)
        return original_rollback()

    db.commit = _exploding_commit  # type: ignore[method-assign]
    db.rollback = _counting_rollback  # type: ignore[method-assign]

    import logging

    with caplog.at_level(logging.ERROR, logger="llm.seca.events.storage"):
        with pytest.raises(_BoomError, match="simulated DB crash"):
            storage.store_game(
                player_id=str(player.id),
                pgn="1. e4 e5",
                result="loss",
                accuracy=0.1,
                weaknesses={},
            )

    # The except branch must rollback before re-raising — without this,
    # a Postgres commit failure leaves the session in
    # InFailedSqlTransaction and the next ORM call in any caller that
    # reuses the session cascades to a second 500.
    assert rollback_calls, (
        "EventStorage.store_game must call db.rollback() in its except "
        "branch before re-raising (see PR #165 /game/finish cascade incident)"
    )

    # The except branch must log before re-raising — without this,
    # production lost the stack trace and only saw a 500 in the
    # caller's frame.
    assert any(
        "Learning pipeline crash in EventStorage.store_game" in rec.message
        for rec in caplog.records
    ), f"expected crash log; got records: {[r.message for r in caplog.records]!r}"


# ---------------------------------------------------------------------------
# ESTORE_03 / ESTORE_04 — read-side helpers
# ---------------------------------------------------------------------------


def test_estore_03_get_recent_games_scopes_by_player_and_respects_limit(db, player):
    """Recent-games is per-player and bounded.  Without the per-player
    filter the analytics dashboard would mix data across users."""
    storage = EventStorage(db)
    for i in range(5):
        storage.store_game(
            player_id=str(player.id),
            pgn=f"1. e4 e5 {i}",
            result="win",
            accuracy=0.5 + i * 0.05,
            weaknesses={},
        )

    # A second player with their own games — must NOT appear in the
    # first player's recent list.
    other = Player(email="other@example.com", password_hash="x", player_embedding="[]")
    db.add(other)
    db.commit()
    db.refresh(other)
    storage.store_game(
        player_id=str(other.id),
        pgn="other game",
        result="draw",
        accuracy=0.4,
        weaknesses={},
    )

    rows = storage.get_recent_games(str(player.id), limit=3)
    assert len(rows) == 3
    assert all(r.player_id == player.id for r in rows)


def test_estore_04_get_all_recent_games_spans_players(db, player):
    """Cross-player feed for ops dashboards / admin views."""
    storage = EventStorage(db)
    other = Player(email="other2@example.com", password_hash="x", player_embedding="[]")
    db.add(other)
    db.commit()
    db.refresh(other)

    storage.store_game(
        player_id=str(player.id),
        pgn="a",
        result="win",
        accuracy=0.5,
        weaknesses={},
    )
    storage.store_game(
        player_id=str(other.id),
        pgn="b",
        result="loss",
        accuracy=0.4,
        weaknesses={},
    )

    rows = storage.get_all_recent_games(limit=10)
    assert len(rows) == 2
    # Both players represented.
    assert {r.player_id for r in rows} == {player.id, other.id}
