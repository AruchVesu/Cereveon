"""Tests for the game-review service + HTTP surface.

Covers ``llm.seca.review.service`` (eligibility, job lifecycle, stage
idempotency, entitlement gating, janitor) and ``llm.seca.review.router``
(ownership, error translation, status-code semantics).  Router-layer
tests call handlers directly with a stub request — the same pattern as
``test_lichess_import.py`` — so no live app, engine, or LLM is needed.

Pinned invariants
-----------------
SV_01  check_eligibility rejects in-app games (code=not_lichess).
SV_02  check_eligibility rejects short games (code=too_short).
SV_03  start_review creates a queued row and dispatches exactly once.
SV_04  start_review coalesces onto an active row without re-dispatch.
SV_05  run_review_job full pass: engine stage → moments → LLM →
       complete; banded series wire shape; blunder moment found.
SV_06  run_review_job without a pool fails the row loudly.
SV_07  Entitlement cap (free plan, 3/month) skips the LLM stage;
       engine content still lands (never 402, never blocked Wave 2).
SV_08  Same-game retry re-admits idempotently (no double charge).
SV_09  LLM-retry path re-runs Wave 3 without re-running the engine
       stage (fallback outcome → engine stage skipped on second run).
SV_10  Janitor: queued/running → failed; engine_done → complete with
       deterministic fallback texts.
SV_11  Engine stage backfills unscored GameEvent accuracy/weaknesses.

RT_01  POST unknown event → 404.
RT_02  POST another player's event → 403 (probe-visible).
RT_03  POST oversized event_id → 400 before any DB lookup.
RT_04  POST in-app game → 400 with machine-readable code.
RT_05  POST valid game → 202 + serialized queued row + entitlement.
RT_06  GET with no review row → 404.
RT_07  GET serves the row with parsed JSON payload columns.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime

import chess
import chess.pgn
import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request as StarletteRequest

# Import all model modules so Base.metadata sees every table.
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401
import llm.seca.lichess.models  # noqa: F401
import llm.seca.entitlements.models  # noqa: F401
import llm.seca.review.models  # noqa: F401

from llm.rag.llm.base import BaseLLM
from llm.seca.auth.models import Base, Player
from llm.seca.entitlements import service as entitlements
from llm.seca.entitlements.models import UsageCounter
from llm.seca.events.models import GameEvent
from llm.seca.review import service as review_service
from llm.seca.review.models import (
    GameReview,
    LLM_OUTCOME_FALLBACK,
    LLM_OUTCOME_FULL,
    LLM_OUTCOME_SKIPPED_ENTITLEMENT,
    REVIEW_STATUS_COMPLETE,
    REVIEW_STATUS_ENGINE_DONE,
    REVIEW_STATUS_FAILED,
    REVIEW_STATUS_QUEUED,
)
from llm.seca.review.moments import MOMENT_BLUNDER
from llm.seca.review.router import get_review, start_review
from llm.seca.shared_limiter import limiter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_setup():
    """In-memory engine on a StaticPool so the worker's own session (a
    second connection otherwise) sees the same database — the
    TestClient/worker-thread gotcha documented in the test guides."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return engine, Session


@pytest.fixture()
def db_session(db_setup):
    _, Session = db_setup
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def worker_session(db_setup, monkeypatch):
    """Point the service's _WorkerSession at the test database."""
    _, Session = db_setup
    monkeypatch.setattr(review_service, "_WorkerSession", Session)
    return Session


@pytest.fixture()
def player(db_session):
    p = Player(
        email="rev@test.com",
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
        email="other@test.com",
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


# 31 legal plies (Ruy Lopez, Breyer) — clears MIN_REVIEW_PLIES=30.
_BREYER_SANS = (
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O h3 Nb8 "
    "d4 Nbd7 Nbd2 Bb7 Bc2 Re8 Nf1 Bf8 Ng3 g6 a4 c5 d5"
).split()


def _make_pgn(moves_san: list[str], *, result: str = "0-1") -> str:
    game = chess.pgn.Game()
    board = chess.Board()
    node: chess.pgn.GameNode = game
    for san in moves_san:
        move = board.parse_san(san)
        node = node.add_variation(move)
        board.push(move)
    game.headers["White"] = "revplayer"
    game.headers["Black"] = "opponent1234"
    game.headers["WhiteElo"] = "1200"
    game.headers["BlackElo"] = "1234"
    game.headers["TimeControl"] = "600+5"
    game.headers["Opening"] = "Ruy Lopez: Breyer"
    game.headers["ECO"] = "C95"
    game.headers["Result"] = result
    return str(game)


@pytest.fixture()
def lichess_event(db_session, player):
    ev = GameEvent(
        player_id=str(player.id),
        pgn=_make_pgn(_BREYER_SANS, result="0-1"),
        result="loss",
        accuracy=None,
        weaknesses_json="{}",
        source="lichess",
        external_game_id="revgame1",
        player_color="white",
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    return ev


@pytest.fixture()
def app_event(db_session, player):
    ev = GameEvent(
        player_id=str(player.id),
        pgn=_make_pgn(_BREYER_SANS, result="1-0"),
        result="win",
        source="app",
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    return ev


@pytest.fixture()
def short_event(db_session, player):
    ev = GameEvent(
        player_id=str(player.id),
        pgn=_make_pgn(["e4", "e5", "Nf3", "Nc6"], result="1-0"),
        result="win",
        source="lichess",
        external_game_id="shortgame1",
        player_color="white",
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    return ev


class _SeqEvalPool:
    """Scripted evaluate_position: returns queued cp values in call
    order (start position first, then after each ply), then 0s for any
    later probes (the LLM stage's ESV lookups)."""

    def __init__(self, evals_cp: list[int]):
        self.evals_cp = list(evals_cp)
        self.calls = 0

    def evaluate_position(self, *, fen, movetime_ms, queue_timeout_ms=None):
        self.calls += 1
        value = self.evals_cp.pop(0) if self.evals_cp else 0
        return {"evaluation": {"type": "cp", "value": value}}


def _blunder_pool() -> _SeqEvalPool:
    # Equal through ply 20; White's ply-21 move collapses to -400.
    return _SeqEvalPool([0] * 21 + [-400] * 11)


_COMPLIANT_TEXT = (
    "You kept your pieces working together here and the pressure told. "
    "Next time, take one extra breath to look at your opponent's reply "
    "before committing yourself."
)


class _ScriptedLLM(BaseLLM):
    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or [_COMPLIANT_TEXT])
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


def _fake_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/game/x/review",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


def _fake_response():
    from fastapi import Response

    return Response()


@contextmanager
def _limiter_disabled():
    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev


@pytest.fixture()
def no_dispatch(monkeypatch):
    """Swallow router executor submissions; record them for assertions."""
    submitted: list[tuple] = []

    class _FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    from llm.seca.review import router as review_router

    monkeypatch.setattr(review_router, "_executor", _FakeExecutor())
    return submitted


# ---------------------------------------------------------------------------
# SV — service layer
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_app_game_rejected(self, app_event):
        """SV_01"""
        with pytest.raises(review_service.ReviewEligibilityError) as exc:
            review_service.check_eligibility(app_event)
        assert exc.value.code == "not_lichess"

    def test_short_game_rejected(self, short_event):
        """SV_02"""
        with pytest.raises(review_service.ReviewEligibilityError) as exc:
            review_service.check_eligibility(short_event)
        assert exc.value.code == "too_short"


class TestStartReview:
    def test_creates_and_dispatches(self, db_session, player, lichess_event):
        """SV_03"""
        dispatched: list[str] = []
        review, created = review_service.start_review(
            db_session, player, lichess_event, dispatch=dispatched.append
        )
        assert created is True
        assert review.status == REVIEW_STATUS_QUEUED
        assert dispatched == [review.id]

    def test_coalesces_active_row(self, db_session, player, lichess_event):
        """SV_04"""
        first, _ = review_service.start_review(
            db_session, player, lichess_event, dispatch=lambda _id: None
        )
        dispatched: list[str] = []
        second, created = review_service.start_review(
            db_session, player, lichess_event, dispatch=dispatched.append
        )
        assert created is False
        assert second.id == first.id
        assert dispatched == []


class TestRunReviewJob:
    def test_full_pass(self, db_session, player, lichess_event, worker_session):
        """SV_05 + SV_11"""
        review, _ = review_service.start_review(db_session, player, lichess_event)
        pool = _blunder_pool()
        llm = _ScriptedLLM()

        review_service.run_review_job(review.id, engine_pool=pool, llm=llm)

        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_COMPLETE
        assert row.completed_at is not None

        engine_payload = json.loads(row.engine_json)
        # 31 plies → 32 positions, every entry a band string, no numbers.
        assert len(engine_payload["bands"]) == 32
        assert set(engine_payload["bands"]) <= {"losing", "worse", "equal", "better", "winning"}
        assert engine_payload["player_color"] == "white"
        assert engine_payload["meta"]["opening"].startswith("Ruy Lopez")
        assert engine_payload["counts"]["blunders"] == 1

        moments = json.loads(row.moments_json)
        assert len(moments) == 1
        assert moments[0]["ply"] == 21
        assert moments[0]["moment_type"] == MOMENT_BLUNDER
        assert moments[0]["band_before"] == "equal"
        assert moments[0]["band_after"] == "losing"

        llm_payload = json.loads(row.llm_json)
        assert llm_payload["outcome"] == LLM_OUTCOME_FULL
        assert llm_payload["moments"][0]["ply"] == 21
        assert llm_payload["verdict"]["text"]
        # One call per moment + one verdict.
        assert llm.calls == 2

        # SV_11 — the unscored imported row was backfilled from the same pass.
        db_session.refresh(lichess_event)
        assert lichess_event.accuracy is not None and lichess_event.accuracy > 0.0

    def test_pool_missing_fails_row(self, db_session, player, lichess_event, worker_session):
        """SV_06"""
        review, _ = review_service.start_review(db_session, player, lichess_event)
        review_service.run_review_job(review.id, engine_pool=None, llm=_ScriptedLLM())
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_FAILED
        assert "engine pool" in (row.error_message or "")

    def test_entitlement_cap_skips_llm_stage(
        self, db_session, player, lichess_event, worker_session, monkeypatch
    ):
        """SV_07 — free plan at its monthly cap: Wave 2 lands, Wave 3 is
        skipped with the machine-readable outcome; nothing 402s."""
        monkeypatch.setattr(entitlements, "resolve_enforced", lambda: True)
        period = datetime.utcnow().strftime("%Y-%m")
        for i in range(3):
            db_session.add(
                UsageCounter(
                    player_id=player.id,
                    metric=entitlements.METRIC_IMPORT_ANALYSIS,
                    period_key=period,
                    subject=f"other-game-{i}",
                    count=1,
                )
            )
        db_session.commit()

        review, _ = review_service.start_review(db_session, player, lichess_event)
        llm = _ScriptedLLM()
        review_service.run_review_job(review.id, engine_pool=_blunder_pool(), llm=llm)

        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_COMPLETE
        assert row.engine_json is not None
        assert json.loads(row.llm_json)["outcome"] == LLM_OUTCOME_SKIPPED_ENTITLEMENT
        assert llm.calls == 0

    def test_same_game_readmission_is_free(
        self, db_session, player, lichess_event, worker_session, monkeypatch
    ):
        """SV_08 — the review's own game already holds an admission
        marker: a re-run is allowed even with the rest of the cap used."""
        monkeypatch.setattr(entitlements, "resolve_enforced", lambda: True)
        period = datetime.utcnow().strftime("%Y-%m")
        db_session.add(
            UsageCounter(
                player_id=player.id,
                metric=entitlements.METRIC_IMPORT_ANALYSIS,
                period_key=period,
                subject=str(lichess_event.id),
                count=1,
            )
        )
        for i in range(2):
            db_session.add(
                UsageCounter(
                    player_id=player.id,
                    metric=entitlements.METRIC_IMPORT_ANALYSIS,
                    period_key=period,
                    subject=f"other-game-{i}",
                    count=1,
                )
            )
        db_session.commit()

        review, _ = review_service.start_review(db_session, player, lichess_event)
        review_service.run_review_job(
            review.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM()
        )
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert json.loads(row.llm_json)["outcome"] == LLM_OUTCOME_FULL

    def test_llm_retry_skips_engine_stage(
        self, db_session, player, lichess_event, worker_session
    ):
        """SV_09 — a complete row with a fallback outcome re-runs Wave 3
        only: the second run gets NO engine pool and still completes."""
        review, _ = review_service.start_review(db_session, player, lichess_event)
        # First run: LLM produces notation → both attempts rejected → fallback.
        review_service.run_review_job(
            review.id,
            engine_pool=_blunder_pool(),
            llm=_ScriptedLLM(["Play Nf3 and win the knight."]),
        )
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        first_engine_json = row.engine_json
        assert json.loads(row.llm_json)["outcome"] == LLM_OUTCOME_FALLBACK

        dispatched: list[str] = []
        row2, created = review_service.start_review(
            db_session, player, lichess_event, dispatch=dispatched.append
        )
        assert created is True
        assert row2.status == REVIEW_STATUS_ENGINE_DONE
        assert dispatched == [row2.id]

        # engine_pool=None proves the engine stage is skipped: it would
        # fail the row if it ran.  ESV probes tolerate a missing pool.
        review_service.run_review_job(row2.id, engine_pool=None, llm=_ScriptedLLM())
        db_session.expire_all()
        row3 = db_session.get(GameReview, review.id)
        assert row3.status == REVIEW_STATUS_COMPLETE
        assert json.loads(row3.llm_json)["outcome"] == LLM_OUTCOME_FULL
        assert row3.engine_json == first_engine_json

    def test_janitor_sweeps_and_completes(
        self, db_session, player, lichess_event, worker_session
    ):
        """SV_10"""
        queued = GameReview(
            game_event_id=lichess_event.id, player_id=str(player.id), status=REVIEW_STATUS_QUEUED
        )
        db_session.add(queued)
        db_session.commit()

        # Drive a second event to engine_done, then strand it.
        stranded_event = GameEvent(
            player_id=str(player.id),
            pgn=_make_pgn(_BREYER_SANS, result="0-1"),
            result="loss",
            source="lichess",
            external_game_id="strandedgame",
            player_color="white",
        )
        db_session.add(stranded_event)
        db_session.commit()
        stranded, _ = review_service.start_review(db_session, player, stranded_event)
        review_service.run_review_job(
            stranded.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM()
        )
        db_session.expire_all()
        stranded_row = db_session.get(GameReview, stranded.id)
        stranded_row.status = REVIEW_STATUS_ENGINE_DONE
        stranded_row.llm_json = None
        db_session.commit()

        swept = review_service.cleanup_stale_reviews_on_startup()
        assert swept == 2

        db_session.expire_all()
        assert db_session.get(GameReview, queued.id).status == REVIEW_STATUS_FAILED
        completed = db_session.get(GameReview, stranded.id)
        assert completed.status == REVIEW_STATUS_COMPLETE
        llm_payload = json.loads(completed.llm_json)
        assert llm_payload["outcome"] == LLM_OUTCOME_FALLBACK
        assert llm_payload["moments"][0]["text"]
        assert llm_payload["verdict"]["text"]


class TestServiceBranches:
    """SV_12+ — defensive branches: worker robustness, corrupt rows,
    janitor edge cases, helper fallbacks."""

    def test_worker_unknown_review_id_returns(self, worker_session):
        """SV_12: bogus id aborts quietly (no raise, nothing written)."""
        review_service.run_review_job("no-such-review", engine_pool=None, llm=_ScriptedLLM())

    def test_worker_skips_already_failed_row(self, db_session, player, lichess_event, worker_session):
        """SV_13: janitor/unlink raced ahead — worker must not clobber."""
        review, _ = review_service.start_review(db_session, player, lichess_event)
        review.status = REVIEW_STATUS_FAILED
        review.error_message = "swept"
        db_session.commit()
        review_service.run_review_job(review.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM())
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_FAILED
        assert row.error_message == "swept"

    def test_worker_missing_event_fails_row(self, db_session, player, worker_session):
        """SV_14: dangling event reference fails the row loudly."""
        review = GameReview(game_event_id="ghost-event", player_id=str(player.id))
        db_session.add(review)
        db_session.commit()
        review_service.run_review_job(review.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM())
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_FAILED
        assert "missing" in (row.error_message or "")

    def test_unparseable_pgn_fails_engine_stage(self, db_session, player, worker_session):
        """SV_15: a row whose PGN cannot be analysed fails with the
        engine-stage message (eligibility is a POST-time check only)."""
        ev = GameEvent(
            player_id=str(player.id),
            pgn='[Event "T"]\n[Result "*"]\n\n*\n',  # moveless
            result="draw",
            source="lichess",
            external_game_id="movelessgame",
        )
        db_session.add(ev)
        db_session.commit()
        review = GameReview(game_event_id=ev.id, player_id=str(player.id))
        db_session.add(review)
        db_session.commit()
        review_service.run_review_job(review.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM())
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_FAILED
        assert "analyzed" in (row.error_message or "")

    def test_llm_stage_crash_marks_row_failed(
        self, db_session, player, lichess_event, worker_session, monkeypatch
    ):
        """SV_16: an unexpected exception inside the job (here: the
        entitlement layer) lands on the outer guard — row failed with a
        truncated message, nothing propagates to the executor."""

        def _boom(*args, **kwargs):
            raise RuntimeError("entitlements exploded " + "x" * 600)

        monkeypatch.setattr(entitlements, "admit", _boom)
        review, _ = review_service.start_review(db_session, player, lichess_event)
        review_service.run_review_job(review.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM())
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_FAILED
        assert len(row.error_message) <= 500

    def test_esv_probe_failure_is_tolerated(self, db_session, player, lichess_event, worker_session):
        """SV_17: a pool that dies AFTER the engine stage (LLM-retry
        path probes) degrades the ESV, not the review."""

        class ExplodingPool:
            def evaluate_position(self, *, fen, movetime_ms, queue_timeout_ms=None):
                raise RuntimeError("pool gone")

        review, _ = review_service.start_review(db_session, player, lichess_event)
        review_service.run_review_job(review.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM())
        db_session.expire_all()
        # Retry Wave 3 with the exploding pool: probes fail, review still
        # completes with LLM texts (validated against the FEN-derived ESV).
        row, _ = review_service.start_review(db_session, player, lichess_event)
        row.status = REVIEW_STATUS_ENGINE_DONE
        db_session.commit()
        review_service.run_review_job(row.id, engine_pool=ExplodingPool(), llm=_ScriptedLLM())
        db_session.expire_all()
        final = db_session.get(GameReview, row.id)
        assert final.status == REVIEW_STATUS_COMPLETE

    def test_serialize_tolerates_corrupt_json_columns(self, db_session, player, lichess_event):
        """SV_18: a corrupt payload column serializes as null instead of
        500ing the poll."""
        review = GameReview(
            game_event_id=lichess_event.id,
            player_id=str(player.id),
            status=REVIEW_STATUS_COMPLETE,
        )
        review.engine_json = "{not json"
        review.llm_json = "also not json"
        db_session.add(review)
        db_session.commit()
        body = review_service.serialize_review(review)
        assert body["engine"] is None
        assert body["llm"] is None
        assert body["status"] == REVIEW_STATUS_COMPLETE

    def test_helper_fallbacks(self):
        """SV_19: qualitative helpers degrade cleanly on junk input."""
        assert review_service._pgn_meta("not a pgn at all \x00") == {}
        assert review_service._accuracy_phrase(0.9).startswith("very steady")
        assert review_service._accuracy_phrase(0.7).startswith("steady")
        assert review_service._accuracy_phrase(0.5).startswith("uneven")
        assert review_service._accuracy_phrase(0.1).startswith("stormy")

        class _Ev:
            weaknesses_json = "{broken"

        assert review_service._weak_phases(_Ev()) == []

        class _Ev2:
            weaknesses_json = json.dumps({"endgame": 0.4, "opening": 0.1, "middlegame": 0})

        assert review_service._weak_phases(_Ev2()) == ["endgame", "opening"]

    def test_janitor_tolerates_corrupt_stranded_row(self, db_session, player, lichess_event, worker_session):
        """SV_20: an engine_done row with corrupt moments_json still
        completes with a fallback verdict (no cards, no crash)."""
        review = GameReview(
            game_event_id=lichess_event.id,
            player_id=str(player.id),
            status=REVIEW_STATUS_ENGINE_DONE,
        )
        review.moments_json = "{corrupt"
        db_session.add(review)
        db_session.commit()
        swept = review_service.cleanup_stale_reviews_on_startup()
        assert swept == 1
        db_session.expire_all()
        row = db_session.get(GameReview, review.id)
        assert row.status == REVIEW_STATUS_COMPLETE
        payload = json.loads(row.llm_json)
        assert payload["moments"] == []
        assert payload["verdict"]["text"]


# ---------------------------------------------------------------------------
# RT — router layer
# ---------------------------------------------------------------------------


class TestRouter:
    def test_post_unknown_event_404(self, db_session, player, no_dispatch):
        """RT_01"""
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            start_review(
                _fake_request(), _fake_response(), "no-such-event", player=player, db=db_session
            )
        assert exc.value.status_code == 404

    def test_post_cross_player_403(self, db_session, other_player, lichess_event, no_dispatch):
        """RT_02"""
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            start_review(
                _fake_request(),
                _fake_response(),
                lichess_event.id,
                player=other_player,
                db=db_session,
            )
        assert exc.value.status_code == 403

    def test_post_oversized_event_id_400(self, db_session, player, no_dispatch):
        """RT_03"""
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            start_review(
                _fake_request(), _fake_response(), "x" * 65, player=player, db=db_session
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "event_id too long"

    def test_post_app_game_400_with_code(self, db_session, player, app_event, no_dispatch):
        """RT_04 — machine-readable eligibility code; no row created."""
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            start_review(
                _fake_request(), _fake_response(), app_event.id, player=player, db=db_session
            )
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "not_lichess"
        assert db_session.query(GameReview).count() == 0

    def test_post_valid_202_with_payload(self, db_session, player, lichess_event, no_dispatch):
        """RT_05"""
        response = _fake_response()
        with _limiter_disabled():
            body = start_review(
                _fake_request(), response, lichess_event.id, player=player, db=db_session
            )
        assert response.status_code == 202
        assert body["status"] == REVIEW_STATUS_QUEUED
        assert body["event_id"] == lichess_event.id
        assert body["engine"] is None
        assert body["entitlement"]["metric"] == "import_analysis"
        assert len(no_dispatch) == 1

    def test_get_without_review_404(self, db_session, player, lichess_event):
        """RT_06"""
        with _limiter_disabled(), pytest.raises(HTTPException) as exc:
            get_review(_fake_request(), lichess_event.id, player=player, db=db_session)
        assert exc.value.status_code == 404

    def test_get_serves_parsed_row(
        self, db_session, player, lichess_event, worker_session, no_dispatch
    ):
        """RT_07"""
        review, _ = review_service.start_review(db_session, player, lichess_event)
        review_service.run_review_job(
            review.id, engine_pool=_blunder_pool(), llm=_ScriptedLLM()
        )
        db_session.expire_all()
        with _limiter_disabled():
            body = get_review(_fake_request(), lichess_event.id, player=player, db=db_session)
        assert body["status"] == REVIEW_STATUS_COMPLETE
        assert isinstance(body["engine"], dict)
        assert isinstance(body["moments"], list)
        assert body["llm"]["outcome"] == LLM_OUTCOME_FULL
        assert body["review_mode"] == "standard"
