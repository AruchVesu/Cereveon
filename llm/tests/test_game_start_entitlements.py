"""Free-tier hard gate on POST /game/start (entitlements).

Free tier = 1 coached game/day, HARD block: once the player has played
their daily game (a coached-game admission marker exists for today), a
new ``/game/start`` returns 402 and no game_id — the client turns that
into a non-dismissible paywall.  Pinned here:

1.  Flag OFF (shipping default): /game/start always creates a game, no
    matter how many markers exist.
2.  Flag ON, free, 0 markers: allowed (starting / resetting before the
    first move is free — a misclick or rethink costs nothing).
3.  Flag ON, free, 1 marker (daily game already played): 402 with the
    documented ``game_daily_limit`` body; no game row created.
4.  Flag ON, pro: NEVER hard-blocked ("Unlimited adaptive games") —
    past the coached-game cap the game still starts and /live/move
    degrades the hints instead (the token ceiling).

Direct endpoint-call style (fake request, limiter disabled, real bound
DB) per test_game_checkpoint.py.  ``start_game`` is sync, so it's called
directly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from fastapi.responses import JSONResponse

from llm.seca.auth.models import Player
from llm.seca.entitlements.models import UsageCounter


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    from llm.tests._storage_test_helpers import bind_temp_database

    bind_temp_database(tmp_path, monkeypatch)
    from llm.seca.auth.router import SessionLocal

    return SessionLocal()


@pytest.fixture()
def no_limiter(monkeypatch):
    from llm.seca.shared_limiter import limiter

    monkeypatch.setattr(limiter, "enabled", False)


def _make_player(db, email: str = "gamer@test.com", plan: str = "free") -> Player:
    p = Player(email=email, password_hash="x", plan=plan)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _seed_game_markers(db, player, count: int) -> None:
    """Write ``count`` distinct coached-game admission markers in the
    current rolling window (what /live/move writes on each game's first
    move).  coached_game is a ROLLING metric — markers carry the sentinel
    bucket and default ``created_at`` (now) so they count in-window."""
    from llm.seca.entitlements import service

    for i in range(count):
        db.add(
            UsageCounter(
                player_id=player.id,
                metric="coached_game",
                period_key=service._ROLLING_PERIOD,
                subject=f"played-game-{i}",
                count=1,
            )
        )
    db.commit()


def _fake_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/game/start",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


def _call(db, player):
    from llm.server import StartGameRequest, start_game

    return start_game(req=StartGameRequest(), request=_fake_request(), player=player, db=db)


def _body_of(response: JSONResponse) -> dict:
    return json.loads(bytes(response.body))


# ---------------------------------------------------------------------------
# 1. Flag off
# ---------------------------------------------------------------------------


class TestFlagOff:
    def test_always_creates_even_with_markers(self, temp_db, no_limiter, monkeypatch):
        monkeypatch.delenv("SECA_ENTITLEMENTS_ENFORCED", raising=False)
        player = _make_player(temp_db)
        _seed_game_markers(temp_db, player, count=5)

        result = _call(temp_db, player)

        assert isinstance(result, dict), "flag off must never 402"
        assert result.get("game_id"), "a game_id must be returned"


# ---------------------------------------------------------------------------
# 2 + 3. Flag on, free plan
# ---------------------------------------------------------------------------


class TestFlagOnFree:
    def test_first_game_allowed_with_no_markers(self, temp_db, no_limiter, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        player = _make_player(temp_db)

        result = _call(temp_db, player)

        assert isinstance(result, dict), "no daily game played yet → allowed"
        assert result.get("game_id")

    def test_second_game_blocked_after_one_played(self, temp_db, no_limiter, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        player = _make_player(temp_db)
        _seed_game_markers(temp_db, player, count=1)  # daily game already played

        result = _call(temp_db, player)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 402
        body = _body_of(result)
        assert set(body.keys()) == {"error", "plan", "limit", "used", "reset_at", "upgrade"}
        assert body["error"] == "game_daily_limit"
        assert body["plan"] == "free"
        assert body["limit"] == 1
        assert body["used"] == 1
        assert body["reset_at"] is not None  # rolling 24h from the played game
        assert body["upgrade"] == {"product": "pro_monthly"}
        assert "x-auth-token" not in result.headers

    def test_no_game_row_created_when_blocked(self, temp_db, no_limiter, monkeypatch):
        from llm.seca.storage.models import Game

        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        player = _make_player(temp_db)
        _seed_game_markers(temp_db, player, count=1)

        _call(temp_db, player)

        temp_db.expire_all()
        rows = temp_db.query(Game).filter(Game.player_id == str(player.id)).count()
        assert rows == 0, "a blocked start must not create a games row"


# ---------------------------------------------------------------------------
# 4. Pro plan — NEVER hard-blocked at /game/start
# ---------------------------------------------------------------------------


class TestFlagOnPro:
    def test_pro_allowed_at_one_game(self, temp_db, no_limiter, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        player = _make_player(temp_db, email="pro@test.com", plan="pro")
        _seed_game_markers(temp_db, player, count=1)

        result = _call(temp_db, player)

        assert isinstance(result, dict), "pro plays unlimited games"
        assert result.get("game_id")

    def test_pro_never_blocked_even_past_coached_cap(self, temp_db, no_limiter, monkeypatch):
        """The paywall sells "Unlimited adaptive games" — a paying
        subscriber must NEVER see the Subscribe block on /game/start.
        Past pro's daily coached-game cap the game still starts; the
        /live/move admission degrades the hints to the deterministic
        coach instead (zero LLM tokens), which is what caps the token
        spend — see entitlements.LIMITS and its service tests."""
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        player = _make_player(temp_db, email="pro10@test.com", plan="pro")
        _seed_game_markers(temp_db, player, count=10)  # coached cap exhausted

        result = _call(temp_db, player)

        assert isinstance(result, dict), "pro over the coached cap still gets a game"
        assert result.get("game_id")
