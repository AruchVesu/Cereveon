"""DB integration test for ``GET /game/{event_id}/positions`` (game replay).

The endpoint replays the stored ``GameEvent.pgn`` into per-ply board FENs +
SANs so the Android game-history review screen can step through the game with
the coaching chat alongside.  Uses an in-memory SQLite database and calls the
route function directly — no HTTP layer, auth, or engine required.

Pinned invariants
-----------------
 1. POSITIONS_START_PLUS_PER_PLY  positions = N+1 FENs (start + one after each ply).
 2. POSITIONS_MOVES_ARE_SAN       moves = the N SANs in order.
 3. POSITIONS_FINAL_MATCHES_PLAY  positions[-1] is the board after the last move.
 4. POSITIONS_OWNERSHIP_403       another player's event -> HTTPException 403.
 5. POSITIONS_NOT_FOUND_404       unknown event_id -> HTTPException 404.
 6. POSITIONS_NO_MOVES_START_ONLY a moveless PGN -> just the start position.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

import chess
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401

from llm.seca.events.models import GameEvent
from llm.seca.events.router import game_positions

_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2026.01.01"]\n'
    '[Round "1"]\n'
    '[White "Tester"]\n'
    '[Black "Bot"]\n'
    '[Result "*"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 *"
)


@pytest.fixture()
def db_session():
    """In-memory SQLite session — torn down after each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _store(db, player_id: str = "p1", pgn: str = _PGN) -> GameEvent:
    ev = GameEvent(player_id=player_id, pgn=pgn, result="loss", accuracy=0.5)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def test_positions_replayed_from_pgn(db_session):
    """POSITIONS_START_PLUS_PER_PLY + MOVES_ARE_SAN + FINAL_MATCHES_PLAY."""
    ev = _store(db_session)
    resp = game_positions(event_id=ev.id, player=SimpleNamespace(id="p1"), db=db_session)

    # 4 plies (e4 e5 Nf3 Nc6) -> 5 positions (start + 4) and 4 SANs.
    assert len(resp["positions"]) == 5
    assert resp["moves"] == ["e4", "e5", "Nf3", "Nc6"]
    assert resp["positions"][0] == chess.STARTING_FEN

    expected = chess.Board()
    for san in resp["moves"]:
        expected.push_san(san)
    assert resp["positions"][-1] == expected.fen()


def test_positions_ownership_403(db_session):
    """POSITIONS_OWNERSHIP_403: a player cannot read another player's game."""
    ev = _store(db_session, player_id="owner")
    with pytest.raises(HTTPException) as exc:
        game_positions(event_id=ev.id, player=SimpleNamespace(id="intruder"), db=db_session)
    assert exc.value.status_code == 403


def test_positions_not_found_404(db_session):
    """POSITIONS_NOT_FOUND_404: unknown event_id is a 404."""
    with pytest.raises(HTTPException) as exc:
        game_positions(event_id="no-such-event", player=SimpleNamespace(id="p1"), db=db_session)
    assert exc.value.status_code == 404


def test_positions_moveless_pgn_returns_start_only(db_session):
    """POSITIONS_NO_MOVES_START_ONLY: a header-only PGN yields just the start."""
    ev = _store(db_session, pgn='[Event "x"]\n[Result "*"]\n\n*')
    resp = game_positions(event_id=ev.id, player=SimpleNamespace(id="p1"), db=db_session)
    assert resp["positions"] == [chess.STARTING_FEN]
    assert resp["moves"] == []
