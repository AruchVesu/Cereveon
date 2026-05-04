"""
Backend tests for the cross-device resume endpoints:
    POST /game/{game_id}/checkpoint
    GET  /game/active

Background
----------
Pre-this-feature: in-progress board state lived only in the Android
client's SharedPreferences.  A device swap / reinstall lost it
entirely.  These endpoints persist the checkpoint server-side so the
client can pull state at cold-start when no local snapshot exists.

Pinned invariants
-----------------
 1. CHECKPOINT_PERSISTS:           POST /game/{id}/checkpoint stores
                                    fen + uci_history on the games row.
 2. CHECKPOINT_REQUIRES_OWNERSHIP: another player can't hijack a
                                    game_id to overwrite state.
 3. CHECKPOINT_REJECTS_FINISHED:   finished games can't be checkpointed
                                    (would create phantom resume entries).
 4. CHECKPOINT_REJECTS_UNKNOWN:    nonexistent game_id → 404.
 5. CHECKPOINT_FEN_BOUNDED:        oversized / control-char FEN → 400.
 6. ACTIVE_RETURNS_LATEST:         GET /game/active returns the most-
                                    recent checkpointed unfinished game.
 7. ACTIVE_404_WHEN_NONE:          no checkpointed games → 404
                                    (= "no resumable game").
 8. ACTIVE_FILTERS_OUT_FINISHED:   a checkpointed game whose row was
                                    later finished is NOT returned.
 9. ACTIVE_FILTERS_BY_PLAYER:      one player's active game is not
                                    visible to another player.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Point storage.db / repo at a temp SQLite file so tests don't
    pollute data/seca.db; tear it down between tests."""
    db_file = tmp_path / "seca-test.db"
    monkeypatch.setattr("llm.seca.storage.db.DB_PATH", db_file)
    # Initialise both raw-sqlite schema (games/moves/explanations) and
    # the SQLAlchemy auth tables we need (players for the FK).
    from llm.seca.storage.db import init_db
    init_db()
    # Players FK target — auth tables aren't created by init_db; add
    # a minimal players row directly so the games FK is satisfied.
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS players (id TEXT PRIMARY KEY, email TEXT)"
    )
    conn.commit()
    conn.close()
    yield db_file


def _ensure_player(player_id: str = "player-checkpoint") -> str:
    """Insert a player row through repo.ensure_player so the games FK
    is satisfied without going through the full auth flow."""
    from llm.seca.storage.repo import ensure_player
    ensure_player(player_id)
    return player_id


def _fake_request() -> StarletteRequest:
    return StarletteRequest({
        "type": "http", "method": "POST", "path": "/game/test/checkpoint",
        "headers": [], "client": ("127.0.0.1", 0),
    })


# ---------------------------------------------------------------------------
# 1.  Schema validation
# ---------------------------------------------------------------------------


class TestCheckpointRequestValidation:
    """GameCheckpointRequest enforces bounds + control-char defence."""

    def test_minimal_valid_request(self):
        from llm.server import GameCheckpointRequest
        req = GameCheckpointRequest(fen="r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
        assert req.uci_history == ""

    def test_with_uci_history(self):
        from llm.server import GameCheckpointRequest
        req = GameCheckpointRequest(
            fen="r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
            uci_history="e2e4,e7e5,g1f3,b8c6,f1c4,g8f6",
        )
        assert "e2e4" in req.uci_history

    def test_blank_fen_rejected(self):
        from llm.server import GameCheckpointRequest
        for bad in ("", "   "):
            with pytest.raises(ValidationError, match="fen must not be empty"):
                GameCheckpointRequest(fen=bad)

    def test_oversized_fen_rejected(self):
        from llm.server import GameCheckpointRequest
        with pytest.raises(ValidationError, match="fen too long"):
            GameCheckpointRequest(fen="x" * 257)

    def test_oversized_uci_history_rejected(self):
        from llm.server import GameCheckpointRequest
        with pytest.raises(ValidationError, match="uci_history too long"):
            GameCheckpointRequest(fen="x", uci_history="a" * 16_385)

    @pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b", "a\x7fb"])
    def test_fen_control_chars_rejected(self, bad):
        from llm.server import GameCheckpointRequest
        with pytest.raises(ValidationError, match="control characters"):
            GameCheckpointRequest(fen=bad)

    @pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b"])
    def test_uci_history_control_chars_rejected(self, bad):
        from llm.server import GameCheckpointRequest
        with pytest.raises(ValidationError, match="control characters"):
            GameCheckpointRequest(fen="ok", uci_history=bad)


# ---------------------------------------------------------------------------
# 2.  Repo behaviour (against a real temp SQLite db)
# ---------------------------------------------------------------------------


class TestCheckpointRepo:
    def test_checkpoint_then_get_active_roundtrip(self, temp_db):
        """CHECKPOINT_PERSISTS + ACTIVE_RETURNS_LATEST."""
        from llm.seca.storage.repo import (
            checkpoint_game, create_game, get_active_game,
        )
        player_id = _ensure_player()
        game_id = create_game(player_id)

        assert checkpoint_game(game_id, "fen-1", "e2e4") is True

        active = get_active_game(player_id)
        assert active is not None
        assert active["game_id"] == game_id
        assert active["current_fen"] == "fen-1"
        assert active["current_uci_history"] == "e2e4"

    def test_checkpoint_overwrites(self, temp_db):
        from llm.seca.storage.repo import (
            checkpoint_game, create_game, get_active_game,
        )
        player_id = _ensure_player()
        game_id = create_game(player_id)
        checkpoint_game(game_id, "fen-1", "e2e4")
        checkpoint_game(game_id, "fen-2", "e2e4,e7e5")

        active = get_active_game(player_id)
        assert active["current_fen"] == "fen-2"
        assert active["current_uci_history"] == "e2e4,e7e5"

    def test_get_active_returns_none_with_no_checkpoint(self, temp_db):
        """ACTIVE_404_WHEN_NONE — a game with no checkpoint shouldn't
        be returned (avoids returning rows for /game/start where the
        user never played a single move)."""
        from llm.seca.storage.repo import create_game, get_active_game
        player_id = _ensure_player()
        create_game(player_id)  # row exists but no checkpoint

        assert get_active_game(player_id) is None

    def test_checkpoint_finished_game_returns_false(self, temp_db):
        """CHECKPOINT_REJECTS_FINISHED at the repo layer."""
        from llm.seca.storage.repo import (
            checkpoint_game, create_game, finish_game,
        )
        player_id = _ensure_player()
        game_id = create_game(player_id)
        finish_game(game_id, "win")

        assert checkpoint_game(game_id, "fen", "uci") is False

    def test_get_active_excludes_finished_games(self, temp_db):
        """ACTIVE_FILTERS_OUT_FINISHED — once finish_game runs, the
        row drops out of the active query even if its checkpoint
        columns are still populated."""
        from llm.seca.storage.repo import (
            checkpoint_game, create_game, finish_game, get_active_game,
        )
        player_id = _ensure_player()
        game_id = create_game(player_id)
        checkpoint_game(game_id, "fen", "e2e4")

        assert get_active_game(player_id) is not None  # before finish
        finish_game(game_id, "win")
        assert get_active_game(player_id) is None       # after finish

    def test_get_active_filters_by_player(self, temp_db):
        """ACTIVE_FILTERS_BY_PLAYER — player A's active game must not
        leak to player B."""
        from llm.seca.storage.repo import (
            checkpoint_game, create_game, get_active_game,
        )
        player_a = _ensure_player("player-a")
        player_b = _ensure_player("player-b")
        a_game = create_game(player_a)
        checkpoint_game(a_game, "fen-a", "e2e4")

        # B has no games — should still see no active game.
        assert get_active_game(player_b) is None

    def test_get_active_returns_latest_checkpoint(self, temp_db):
        """When a player has multiple unfinished games, the most-
        recently-checkpointed one wins."""
        import time
        from llm.seca.storage.repo import (
            checkpoint_game, create_game, get_active_game,
        )
        player_id = _ensure_player()
        old_game = create_game(player_id)
        new_game = create_game(player_id)

        checkpoint_game(old_game, "fen-old", "e2e4")
        # SQLite CURRENT_TIMESTAMP has 1-second resolution; sleep
        # briefly to ensure new_game's checkpoint timestamp wins.
        time.sleep(1.1)
        checkpoint_game(new_game, "fen-new", "d2d4")

        active = get_active_game(player_id)
        assert active["game_id"] == new_game
        assert active["current_fen"] == "fen-new"


# ---------------------------------------------------------------------------
# 3.  Endpoint behaviour
# ---------------------------------------------------------------------------


def _player_namespace(id="player-checkpoint"):
    from types import SimpleNamespace
    return SimpleNamespace(id=id)


def _disable_limiter():
    from llm.seca.shared_limiter import limiter
    return limiter


class TestCheckpointEndpoint:
    """POST /game/{game_id}/checkpoint behaviour."""

    def test_happy_path_returns_status(self, temp_db):
        from llm.server import GameCheckpointRequest, checkpoint_game_state
        from llm.seca.storage.repo import create_game

        player_id = _ensure_player()
        game_id = create_game(player_id)
        player = _player_namespace(player_id)

        limiter = _disable_limiter()
        prev = limiter.enabled
        limiter.enabled = False
        try:
            result = checkpoint_game_state(
                game_id=game_id,
                req=GameCheckpointRequest(fen="some-fen", uci_history="e2e4"),
                request=_fake_request(),
                player=player,
            )
        finally:
            limiter.enabled = prev
        assert result == {"status": "checkpointed"}

    def test_unknown_game_returns_404(self, temp_db):
        """CHECKPOINT_REJECTS_UNKNOWN."""
        from llm.server import GameCheckpointRequest, checkpoint_game_state

        _ensure_player()
        player = _player_namespace()
        limiter = _disable_limiter()
        prev = limiter.enabled
        limiter.enabled = False
        try:
            with pytest.raises(HTTPException) as exc:
                checkpoint_game_state(
                    game_id="game-never-existed",
                    req=GameCheckpointRequest(fen="fen"),
                    request=_fake_request(),
                    player=player,
                )
        finally:
            limiter.enabled = prev
        assert exc.value.status_code == 404

    def test_other_players_game_returns_403(self, temp_db):
        """CHECKPOINT_REQUIRES_OWNERSHIP."""
        from llm.server import GameCheckpointRequest, checkpoint_game_state
        from llm.seca.storage.repo import create_game

        owner_id = _ensure_player("player-owner")
        attacker_id = _ensure_player("player-attacker")
        game_id = create_game(owner_id)
        attacker = _player_namespace(attacker_id)

        limiter = _disable_limiter()
        prev = limiter.enabled
        limiter.enabled = False
        try:
            with pytest.raises(HTTPException) as exc:
                checkpoint_game_state(
                    game_id=game_id,
                    req=GameCheckpointRequest(fen="fen"),
                    request=_fake_request(),
                    player=attacker,
                )
        finally:
            limiter.enabled = prev
        assert exc.value.status_code == 403

    def test_finished_game_returns_409(self, temp_db):
        """CHECKPOINT_REJECTS_FINISHED at the endpoint layer."""
        from llm.server import GameCheckpointRequest, checkpoint_game_state
        from llm.seca.storage.repo import create_game, finish_game

        player_id = _ensure_player()
        game_id = create_game(player_id)
        finish_game(game_id, "win")
        player = _player_namespace(player_id)

        limiter = _disable_limiter()
        prev = limiter.enabled
        limiter.enabled = False
        try:
            with pytest.raises(HTTPException) as exc:
                checkpoint_game_state(
                    game_id=game_id,
                    req=GameCheckpointRequest(fen="fen"),
                    request=_fake_request(),
                    player=player,
                )
        finally:
            limiter.enabled = prev
        assert exc.value.status_code == 409

    def test_game_id_too_long_rejected(self, temp_db):
        from llm.server import GameCheckpointRequest, checkpoint_game_state
        _ensure_player()
        player = _player_namespace()
        limiter = _disable_limiter()
        prev = limiter.enabled
        limiter.enabled = False
        try:
            with pytest.raises(HTTPException) as exc:
                checkpoint_game_state(
                    game_id="x" * 65,
                    req=GameCheckpointRequest(fen="fen"),
                    request=_fake_request(),
                    player=player,
                )
        finally:
            limiter.enabled = prev
        assert exc.value.status_code == 400


class TestActiveGameEndpoint:
    """GET /game/active behaviour."""

    def test_returns_active_game(self, temp_db):
        from llm.server import active_game
        from llm.seca.storage.repo import checkpoint_game, create_game

        player_id = _ensure_player()
        game_id = create_game(player_id)
        checkpoint_game(game_id, "fen-active", "e2e4,e7e5")
        player = _player_namespace(player_id)

        limiter = _disable_limiter()
        prev = limiter.enabled
        limiter.enabled = False
        try:
            result = active_game(request=_fake_request(), player=player)
        finally:
            limiter.enabled = prev
        assert result["game_id"] == game_id
        assert result["current_fen"] == "fen-active"
        assert result["current_uci_history"] == "e2e4,e7e5"

    def test_404_when_no_active_game(self, temp_db):
        """ACTIVE_404_WHEN_NONE."""
        from llm.server import active_game

        _ensure_player()
        player = _player_namespace()

        limiter = _disable_limiter()
        prev = limiter.enabled
        limiter.enabled = False
        try:
            with pytest.raises(HTTPException) as exc:
                active_game(request=_fake_request(), player=player)
        finally:
            limiter.enabled = prev
        assert exc.value.status_code == 404
