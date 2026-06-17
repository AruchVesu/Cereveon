"""End-to-end alignment test for per-game coaching chat (game-history screen).

Why this exists
---------------
The Android game-history screen (PR #231) lets a user expand a past game to
see that game's coaching conversation. It does so by reading ``game_id`` off
each ``GET /game/history`` row and re-sending it as
``GET /chat/history?game_id=<that value>``.

Three independent backend steps must agree on ONE identifier for that to work:

    live chat   -> save_exchange(game_id=G)      => chat_turns.game_id   = G
    game finish -> store_game(app_game_id=G)      => game_events.app_game_id = G
    recall      -> game_history() row "game_id"   => G  (projects app_game_id)
                -> recent_turns_for_player(game_id=G) returns that game's turns

Each side already has isolated coverage (test_chat_persistence.py,
test_game_finish_db_integration.py, test_game_finish_resume_link.py). What was
missing — and what this file pins — is that they *compose* with the SAME key.
A drift on either side (e.g. ``/game/history`` projecting the event row id
``str(ev.id)`` instead of ``app_game_id`` — as the unrelated
``/player/progress`` endpoint does) makes the recall return ``[]`` and the
Android expand show "No coaching chat for this game" even though chat exists,
WITHOUT failing any per-side test. This file is that regression guard.

Uses an in-memory SQLite DB and calls the real repo + route functions
directly — no TestClient, auth, engine, or LLM needed.

Pinned invariants
-----------------
 1. ALIGN_ROUNDTRIP            game_id from /game/history, fed straight into
                               /chat/history's filter, returns that game's chat.
 2. ALIGN_PROJECTS_APP_GAME_ID the surfaced game_id is the live game id
                               (app_game_id), never the GameEvent row id.
 3. ALIGN_GAME_SCOPED          another game's turns never leak into the recall.
 4. ALIGN_NO_CHAT_EMPTY        a finished game linked to a game_id but with no
                               chat recalls [] (the client's empty-state path).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import every model so Base.metadata is complete before create_all().
# Importing ChatTurn explicitly is what registers the chat_turns table here
# (test_game_finish_db_integration omits it because it never touches chat).
from llm.seca.auth.models import Base
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401
from llm.seca.chat.models import ChatTurn  # noqa: F401

from llm.seca.chat.repo import recent_turns_for_player, save_exchange
from llm.seca.events.router import game_history
from llm.seca.events.storage import EventStorage

_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "2026.01.01"]\n'
    '[Round "1"]\n'
    '[White "Tester"]\n'
    '[Black "Bot"]\n'
    '[Result "0-1"]\n'
    "\n"
    "1. e4 e5 2. Nf3 Nc6 0-1"
)
_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture()
def db_session():
    """In-memory SQLite session — torn down after each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_game_history_game_id_round_trips_to_chat_history(db_session):
    """ALIGN_ROUNDTRIP + ALIGN_PROJECTS_APP_GAME_ID + ALIGN_GAME_SCOPED.

    Walk the exact path the Android client walks for a past game and assert
    the expand reveals that game's chat.
    """
    player = "player-e2e"
    game_id = "live-game-7"

    # 1. Live play: a coaching exchange persisted scoped to this game...
    save_exchange(
        db=db_session,
        player_id=player,
        user_content="why is my king unsafe here?",
        assistant_content="Black has a decisive advantage; the king is exposed.",
        fen=_START_FEN,
        game_id=game_id,
    )
    # ...and an exchange in a DIFFERENT game that must NOT leak into this thread.
    save_exchange(
        db=db_session,
        player_id=player,
        user_content="unrelated other-game question",
        assistant_content="unrelated other-game reply",
        fen=_START_FEN,
        game_id="some-other-game",
    )

    # 2. Game finish links the finished game to that same live game id.
    EventStorage(db_session).store_game(
        player_id=player,
        pgn=_VALID_PGN,
        result="loss",
        accuracy=0.42,
        weaknesses={},
        app_game_id=game_id,
    )

    # 3. Recall, exactly as PR #231 does: read game_id off the history row...
    games = game_history(player=SimpleNamespace(id=player), db=db_session)["games"]
    assert len(games) == 1
    row = games[0]
    surfaced_game_id = row["game_id"]

    # ALIGN_PROJECTS_APP_GAME_ID: the surfaced id is the live game id, not the
    # GameEvent row id. If this regresses to str(ev.id), the recall below 404s
    # to empty and the Android expand shows "No coaching chat for this game".
    assert surfaced_game_id == game_id, (
        f"/game/history must surface app_game_id under 'game_id'; got "
        f"{surfaced_game_id!r} (event row id {row.get('id')!r})."
    )

    # ...then fetch that game's chat with the SURFACED id (not the original).
    turns = recent_turns_for_player(db_session, player, game_id=surfaced_game_id)

    # ALIGN_ROUNDTRIP: the two turns of this game's exchange come back.
    assert len(turns) == 2, (
        f"expanding the game must return its 2 chat turns; got {len(turns)} — "
        f"the game_id broke between persistence and recall."
    )
    assert {t.role for t in turns} == {"user", "assistant"}
    # ALIGN_GAME_SCOPED: the other game's turns never bleed in.
    assert all(t.game_id == game_id for t in turns)
    contents = {t.content for t in turns}
    assert "unrelated other-game question" not in contents
    assert "why is my king unsafe here?" in contents


def test_finished_game_with_link_but_no_chat_recalls_empty(db_session):
    """ALIGN_NO_CHAT_EMPTY: a game finished with a game_id but no coaching chat
    recalls [] — the row is expandable but reveals the client's empty state
    ("No coaching chat for this game."), never another game's turns."""
    player = "player-e2e"
    game_id = "live-game-silent"

    # A different game DID have chat — it must not leak into the silent game.
    save_exchange(
        db=db_session,
        player_id=player,
        user_content="chatty game question",
        assistant_content="chatty game reply",
        fen=_START_FEN,
        game_id="live-game-chatty",
    )

    EventStorage(db_session).store_game(
        player_id=player,
        pgn=_VALID_PGN,
        result="win",
        accuracy=0.71,
        weaknesses={},
        app_game_id=game_id,
    )

    games = game_history(player=SimpleNamespace(id=player), db=db_session)["games"]
    silent = next(g for g in games if g["game_id"] == game_id)

    turns = recent_turns_for_player(db_session, player, game_id=silent["game_id"])
    assert turns == [], f"a game with no chat must recall []; got {turns!r}"
