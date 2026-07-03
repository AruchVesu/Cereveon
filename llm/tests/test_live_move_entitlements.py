"""Endpoint-tier tests for the /live/move free-tier degrade (Subtask 3).

The route now runs ``entitlements.admit`` before the Mode-1 pipeline and
threads the decision through as ``force_deterministic`` plus the
additive ``coach_tier`` response field.  Pinned here:

1.  Flag OFF (the shipping default): behaviour is unchanged — the LLM
    path is taken for every game, no usage rows are written, and the
    response is today's shape plus a dormant ``coach_tier``
    (``degraded=false``, ``remaining=null``).
2.  Flag ON, free plan: the first distinct ``game_id`` of the day keeps
    the LLM path (and stays on it for later moves); a SECOND distinct
    game_id is answered with ``force_deterministic=True`` (asserted at
    the pipeline seam via a spy — the LLM is skipped, not post-filtered)
    and ``coach_tier.degraded=true`` while ``mode`` stays ``LIVE_V1``.
3.  Missing ``game_id`` fails OPEN even with the flag on (pre-game_id
    clients are never degraded).
4.  The returned dict passes ``validate_live_move_response`` with the
    extra ``coach_tier`` key (boundary leniency to additive fields).
5.  ``LiveMoveRequest.game_id`` validator: None/empty → None, 64-char
    cap enforced, valid ids round-trip (mirrors ``ChatRequest.game_id``).

Test style: direct endpoint-function calls with the shared limiter
disabled and a fake Starlette request — the ``test_game_checkpoint.py``
pattern — so no TestClient/auth stack is needed.  The Mode-1 pipeline is
replaced by a spy that records ``force_deterministic`` and returns a
minimal valid reply; ``engine_pool`` is pinned to ``None`` so no
Stockfish work runs.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.seca.auth.models import Base, Player
from llm.seca.entitlements.models import UsageCounter

_VALID_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"

# Real extractor output so the boundary validator's EngineSignalSchema
# sees an authentic shape (FEN-heuristic path; no engine needed).
_SIGNAL = extract_engine_signal({}, fen=_VALID_FEN)


class _SpyPipeline:
    """Stands in for generate_live_reply; records the force flag."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self,
        fen,
        uci,
        player_id="demo",
        explanation_style=None,
        stockfish_json=None,
        force_deterministic=False,
    ):
        self.calls.append({"force_deterministic": force_deterministic, "uci": uci})
        # Empty hint is contract-legal (API_CONTRACTS.md §4) and skips
        # the Mode-2 content gates, keeping these tests about ROUTING,
        # not hint wording.
        return SimpleNamespace(
            hint="",
            engine_signal=_SIGNAL,
            move_quality="unknown",
            mode="LIVE_V1",
        )


def _fake_request() -> StarletteRequest:
    return StarletteRequest({
        "type": "http", "method": "POST", "path": "/live/move",
        "headers": [], "client": ("127.0.0.1", 0),
    })


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def player(db):
    p = Player(email="live-move@test.com", password_hash="not-used-here")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def spy(monkeypatch):
    import llm.server as server
    from llm.seca.shared_limiter import limiter

    pipeline_spy = _SpyPipeline()
    monkeypatch.setattr(server, "generate_live_reply", pipeline_spy)
    monkeypatch.setattr(server, "engine_pool", None)
    monkeypatch.setattr(limiter, "enabled", False)
    return pipeline_spy


def _call(db, player, game_id=None):
    import llm.server as server

    req = server.LiveMoveRequest(fen=_VALID_FEN, uci="g1f3", game_id=game_id)
    return asyncio.run(
        server.live_move(req=req, request=_fake_request(), player=player, db=db)
    )


# ---------------------------------------------------------------------------
# 1. Flag off — shipping default
# ---------------------------------------------------------------------------


class TestFlagOff:
    def test_llm_path_for_every_game_and_no_rows(self, db, player, spy, monkeypatch):
        monkeypatch.delenv("SECA_ENTITLEMENTS_ENFORCED", raising=False)

        first = _call(db, player, "game-a")
        second = _call(db, player, "game-b")

        assert [c["force_deterministic"] for c in spy.calls] == [False, False]
        for response in (first, second):
            assert response["status"] == "ok"
            assert response["mode"] == "LIVE_V1"
            assert response["hint"] == ""
            assert response["coach_tier"]["degraded"] is False
            assert response["coach_tier"]["plan"] == "free"
            assert response["coach_tier"]["remaining"] is None, (
                "dormant metering must report null remaining (not-metered), never a number"
            )
        assert db.query(UsageCounter).count() == 0, "dormant mode must write no usage rows"


# ---------------------------------------------------------------------------
# 2 + 3. Flag on — free plan admission
# ---------------------------------------------------------------------------


class TestFlagOnFreePlan:
    def test_second_distinct_game_degrades_first_stays_llm(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")

        first = _call(db, player, "game-a")
        assert spy.calls[-1]["force_deterministic"] is False
        assert first["coach_tier"] == {"plan": "free", "degraded": False, "remaining": 0}

        # Later move of the SAME game: still the LLM path.
        again = _call(db, player, "game-a")
        assert spy.calls[-1]["force_deterministic"] is False
        assert again["coach_tier"]["degraded"] is False

        # A SECOND distinct game the same day: deterministic coach.
        second = _call(db, player, "game-b")
        assert spy.calls[-1]["force_deterministic"] is True, (
            "over-quota game must skip the LLM at the pipeline seam"
        )
        assert second["coach_tier"]["degraded"] is True
        assert second["mode"] == "LIVE_V1", "degrade changes the hint source, not the contract"
        assert second["status"] == "ok"

        # And it STAYS degraded on its next move.
        assert _call(db, player, "game-b")["coach_tier"]["degraded"] is True
        assert spy.calls[-1]["force_deterministic"] is True

    def test_missing_game_id_fails_open(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")

        response = _call(db, player, None)

        assert spy.calls[-1]["force_deterministic"] is False
        assert response["coach_tier"]["degraded"] is False
        assert db.query(UsageCounter).count() == 0, "no admission marker without a game_id"


# ---------------------------------------------------------------------------
# 4. Boundary validator leniency
# ---------------------------------------------------------------------------


class TestBoundaryValidator:
    def test_response_with_coach_tier_passes_validator(self, db, player, spy, monkeypatch):
        monkeypatch.setenv("SECA_ENTITLEMENTS_ENFORCED", "true")
        from llm.rag.validators.explain_response_schema import validate_live_move_response

        response = _call(db, player, "game-a")
        assert "coach_tier" in response
        # Must not raise: LiveMoveResponse ignores unknown keys, which is
        # exactly what makes coach_tier additive.
        validate_live_move_response(response)


# ---------------------------------------------------------------------------
# 5. Request-schema validator
# ---------------------------------------------------------------------------


class TestLiveMoveRequestGameId:
    def _req(self, **kwargs):
        from llm.server import LiveMoveRequest

        return LiveMoveRequest(fen=_VALID_FEN, uci="g1f3", **kwargs)

    def test_absent_and_null_normalise_to_none(self):
        assert self._req().game_id is None
        assert self._req(game_id=None).game_id is None

    def test_empty_and_whitespace_normalise_to_none(self):
        assert self._req(game_id="").game_id is None
        assert self._req(game_id="   ").game_id is None

    def test_valid_id_round_trips(self):
        assert self._req(game_id="game-123").game_id == "game-123"
        assert self._req(game_id="x" * 64).game_id == "x" * 64

    def test_over_64_chars_rejected(self):
        with pytest.raises(ValidationError, match="game_id too long"):
            self._req(game_id="x" * 65)
