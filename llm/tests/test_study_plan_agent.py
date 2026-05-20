"""
Backend tests for the per-mistake study-plan agent (phase 1 scaffold).

Covers ``llm.seca.coach.study_plan.agent.generate_plan`` and the
``GET /coach/plan/today`` endpoint in ``llm.seca.coach.study_plan.router``.

Phase 1 is a STUB: the agent writes a 3-puzzle plan (day_offset 0/3/7)
where every puzzle points at the mistake FEN+UCI, theme is
``"generic"``, and the LLM-written verdict is the empty string.  Phases
2-4 light up the verdict, library variants, and Android UI.  These
tests pin the scaffold's contract: the data model, dedup semantics,
scheduling, status transitions, and endpoint shape.

Pinned invariants
-----------------
 1. AGENT_CREATES_PLAN_AND_THREE_PUZZLES   one plan + three puzzles per call.
 2. AGENT_DAY_OFFSETS_ARE_0_3_7            puzzles cover ``PLAN_DAY_OFFSETS``.
 3. AGENT_DUE_AT_CHRONOLOGICAL             due_at values are now / +3d / +7d.
 4. AGENT_STUB_THEME_AND_VERDICT           phase 1 stub: theme="generic", verdict="".
 5. AGENT_ALL_PUZZLES_USE_MISTAKE_FEN      phase 1 stub: every puzzle's fen == mistake_fen.
 6. AGENT_ALL_PUZZLES_USE_PLAYED_UCI       phase 1 stub: every expected_move_uci == played_uci.
 7. AGENT_PUZZLE_SOURCE_TYPE_ORIGINAL      phase 1 stub: source_type="original" everywhere.
 8. AGENT_STATUS_ACTIVE                    new plan lands at status="active".
 9. AGENT_DEDUPS_SAME_EVENT                second call for same (player, event) returns existing plan.
10. AGENT_DEDUP_DOES_NOT_DOUBLE_WRITE      dedup path leaves table at 1 plan + 3 puzzles.
11. TODAY_RETURNS_NONE_WHEN_NO_PLAN        no active plan → endpoint returns None.
12. TODAY_RETURNS_DAY0_WHEN_DUE            fresh plan → today_puzzle is day-0.
13. TODAY_RETURNS_NULL_PUZZLE_WHEN_NONE_DUE day-0 completed + day-3 not yet due → today_puzzle is None.
14. TODAY_RETURNS_LOWEST_DUE_DAY_OFFSET    multiple due puzzles → returns lowest day_offset.
15. TODAY_SKIPS_COMPLETED_PLAN             status="completed" plan not surfaced.
16. TODAY_RETURNS_MOST_RECENT_ACTIVE_PLAN  two active plans → most recent by created_at.
17. TODAY_RESPONSE_SHAPE                   total_days=3, theme="generic", verdict="" in phase 1.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

# Import all model modules so create_all sees every table.
import llm.seca.auth.models  # noqa: F401
import llm.seca.events.models  # noqa: F401
import llm.seca.brain.models  # noqa: F401
import llm.seca.analytics.models  # noqa: F401
import llm.seca.training.models  # noqa: F401
import llm.seca.coach.study_plan.models  # noqa: F401

from llm.seca.auth.models import Base, Player
from llm.seca.events.models import GameEvent
from llm.seca.shared_limiter import limiter
from llm.seca.coach.study_plan.agent import generate_plan
from llm.seca.coach.study_plan.models import (
    PLAN_DAY_OFFSETS,
    PUZZLE_SOURCE_ORIGINAL,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    MistakeStudyPlan,
    MistakeStudyPuzzle,
)
from llm.seca.coach.study_plan.router import get_today_plan

_MISTAKE_FEN = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
_PLAYED_UCI = "f3e5"


def _fake_request() -> StarletteRequest:
    """Minimal Request for slowapi's isinstance check."""
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/coach/plan/today",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session with the full schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
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
        email="planner@test.com",
        password_hash="dummy-hash",
        rating=1500.0,
        confidence=0.5,
        skill_vector_json="{}",
        player_embedding="[]",
        training_xp=0,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def game_event(db_session, player):
    """A GameEvent row to back the study-plan FK."""
    ev = GameEvent(
        player_id=player.id,
        pgn='[Result "0-1"]\n\n1. Nf3 e5 2. Nxe5 0-1',
        result="loss",
        accuracy=0.4,
        weaknesses_json="{}",
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    return ev


def _call_today(player, db):
    """Direct-call the endpoint handler bypassing FastAPI DI; slowapi
    disabled for the duration to match the test_training_solve pattern."""
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        return get_today_plan(
            request=_fake_request(),
            player=player,
            db=db,
        )
    finally:
        limiter.enabled = prev_enabled


# ---------------------------------------------------------------------------
# Agent — generate_plan
# ---------------------------------------------------------------------------


class TestCoachAgentGeneratePlan:
    def test_creates_plan_and_three_puzzles(self, db_session, player, game_event):
        """AGENT_CREATES_PLAN_AND_THREE_PUZZLES — one plan + three puzzles per call."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        assert plan is not None
        assert plan.id is not None

        plans = db_session.query(MistakeStudyPlan).all()
        puzzles = db_session.query(MistakeStudyPuzzle).all()
        assert len(plans) == 1
        assert len(puzzles) == 3

    def test_day_offsets_are_0_3_7(self, db_session, player, game_event):
        """AGENT_DAY_OFFSETS_ARE_0_3_7 — puzzles cover PLAN_DAY_OFFSETS."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        offsets = sorted(p.day_offset for p in plan.puzzles)
        assert tuple(offsets) == PLAN_DAY_OFFSETS

    def test_due_at_chronological(self, db_session, player, game_event):
        """AGENT_DUE_AT_CHRONOLOGICAL — due_at is now / +3d / +7d off
        plan.created_at, in ascending order."""
        before = datetime.utcnow()
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        after = datetime.utcnow()

        # Sort puzzles by day_offset for deterministic comparison.
        by_offset = {p.day_offset: p for p in plan.puzzles}
        day_0 = by_offset[0]
        day_3 = by_offset[3]
        day_7 = by_offset[7]

        # Day-0 due_at lands within the test window.
        assert before <= day_0.due_at <= after
        # Day-3 / day-7 are exactly +3 / +7 days off the same anchor
        # (any small drift between the now() calls would break the
        # equality, but the agent computes both from a single ``now``
        # so they're tightly coupled).
        assert (day_3.due_at - day_0.due_at) == timedelta(days=3)
        assert (day_7.due_at - day_0.due_at) == timedelta(days=7)

    def test_stub_theme_and_verdict(self, db_session, player, game_event):
        """AGENT_STUB_THEME_AND_VERDICT — phase 1 ships generic theme + empty verdict."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        assert plan.theme == "generic"
        assert plan.verdict == ""

    def test_all_puzzles_use_mistake_fen(self, db_session, player, game_event):
        """AGENT_ALL_PUZZLES_USE_MISTAKE_FEN — phase 1 stub repeats the mistake position."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        for puzzle in plan.puzzles:
            assert puzzle.fen == _MISTAKE_FEN

    def test_all_puzzles_use_played_uci(self, db_session, player, game_event):
        """AGENT_ALL_PUZZLES_USE_PLAYED_UCI — phase 1 stub: every expected_move_uci is the user's original bad move."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        for puzzle in plan.puzzles:
            assert puzzle.expected_move_uci == _PLAYED_UCI

    def test_puzzle_source_type_original(self, db_session, player, game_event):
        """AGENT_PUZZLE_SOURCE_TYPE_ORIGINAL — phase 1 stub uses 'original' for every slot."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        for puzzle in plan.puzzles:
            assert puzzle.source_type == PUZZLE_SOURCE_ORIGINAL

    def test_status_active(self, db_session, player, game_event):
        """AGENT_STATUS_ACTIVE — new plan lands at status='active' (phase 1; phase 2 may add pending_generation)."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        assert plan.status == STATUS_ACTIVE

    def test_dedups_same_event(self, db_session, player, game_event):
        """AGENT_DEDUPS_SAME_EVENT — second call for same (player, event) returns existing plan."""
        first = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        second = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        assert first is not None
        assert second is not None
        assert first.id == second.id

    def test_dedup_does_not_double_write(self, db_session, player, game_event):
        """AGENT_DEDUP_DOES_NOT_DOUBLE_WRITE — dedup path leaves table at 1 plan + 3 puzzles."""
        generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )

        plans = db_session.query(MistakeStudyPlan).all()
        puzzles = db_session.query(MistakeStudyPuzzle).all()
        assert len(plans) == 1
        assert len(puzzles) == 3


# ---------------------------------------------------------------------------
# Endpoint — GET /coach/plan/today
# ---------------------------------------------------------------------------


class TestGeneratePlanAsync:
    """Background-task wrapper tests.  ``generate_plan_async`` opens
    its own ``SessionLocal`` session (because the request-scoped one
    is gone by the time FastAPI dispatches the background task), runs
    ``generate_plan``, and must swallow exceptions so a generator
    hiccup never poisons the threadpool worker.

    We monkeypatch ``llm.seca.auth.router.SessionLocal`` to point at
    the test's in-memory engine so the wrapper writes to our
    inspectable DB instead of a real one."""

    def test_async_delegates_to_generate_plan(
        self, db_session, player, game_event, monkeypatch
    ):
        """PLAN_ASYNC_HAPPY_PATH — generate_plan_async writes a plan
        row when given valid inputs.  Verifies the SessionLocal
        injection, the call-through to generate_plan, and the
        commit happened before the wrapper's ``finally`` closed the
        session."""
        from llm.seca.auth import router as auth_router_module
        from llm.seca.coach.study_plan.agent import generate_plan_async

        # Build a SessionLocal-shaped factory that returns the
        # in-memory session ALREADY bound to the test's engine.  The
        # wrapper expects a no-arg callable; ``lambda: db_session``
        # hands back the same session for the test's assertions.  We
        # also patch ``close`` to a no-op so the wrapper's
        # ``finally: db.close()`` doesn't tear down the connection
        # before the test can query.
        original_close = db_session.close
        db_session.close = lambda: None
        monkeypatch.setattr(auth_router_module, "SessionLocal", lambda: db_session)

        try:
            generate_plan_async(
                player_id=player.id,
                source_event_id=game_event.id,
                mistake_fen=_MISTAKE_FEN,
                played_uci=_PLAYED_UCI,
            )
        finally:
            db_session.close = original_close

        plans = db_session.query(MistakeStudyPlan).all()
        puzzles = db_session.query(MistakeStudyPuzzle).all()
        assert len(plans) == 1
        assert len(puzzles) == 3

    def test_async_swallows_exceptions(self, monkeypatch):
        """PLAN_ASYNC_SWALLOWS_EXCEPTION — a raise inside
        ``generate_plan`` (e.g. DB connection failure, FK violation
        from a stale event_id) must NOT propagate out of
        ``generate_plan_async`` — BackgroundTasks runs in the threadpool
        and unhandled exceptions there can poison the worker.  The
        wrapper logs and returns ``None`` instead."""
        from llm.seca.coach.study_plan import agent as agent_module

        # Patch generate_plan to raise; the wrapper should catch.
        def _boom(**kwargs):
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(agent_module, "generate_plan", _boom)
        # Also patch SessionLocal so the wrapper has something to open.
        from llm.seca.auth import router as auth_router_module
        from unittest.mock import MagicMock

        fake_session = MagicMock()
        monkeypatch.setattr(
            auth_router_module, "SessionLocal", lambda: fake_session
        )

        # Must not raise.
        agent_module.generate_plan_async(
            player_id="p1",
            source_event_id="ev1",
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )

        # The wrapper's ``finally: db.close()`` should still fire even
        # when generate_plan raised — verifies the resource cleanup
        # path isn't skipped.
        fake_session.close.assert_called_once()


class TestTodayPlanEndpoint:
    def test_returns_none_when_no_plan(self, db_session, player):
        """TODAY_RETURNS_NONE_WHEN_NO_PLAN — no active plan → endpoint returns None."""
        result = _call_today(player, db_session)
        assert result is None

    def test_returns_day0_when_due(self, db_session, player, game_event):
        """TODAY_RETURNS_DAY0_WHEN_DUE — fresh plan → today_puzzle is day-0."""
        generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )

        result = _call_today(player, db_session)
        assert result is not None
        assert result.today_puzzle is not None
        assert result.today_puzzle.day_offset == 0
        assert result.today_puzzle.fen == _MISTAKE_FEN
        assert result.today_puzzle.expected_move_uci == _PLAYED_UCI
        assert result.today_puzzle.source_type == PUZZLE_SOURCE_ORIGINAL

    def test_returns_null_puzzle_when_none_due(self, db_session, player, game_event):
        """TODAY_RETURNS_NULL_PUZZLE_WHEN_NONE_DUE — day-0 completed +
        day-3 not yet due → today_puzzle is None.  Plan envelope still
        present so the UI can show "next puzzle in N days" copy."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        # Mark day-0 completed; leave day-3 / day-7 in the future as
        # generate_plan scheduled them.
        day_0 = next(p for p in plan.puzzles if p.day_offset == 0)
        day_0.completed_at = datetime.utcnow()
        db_session.commit()

        result = _call_today(player, db_session)
        assert result is not None
        assert result.today_puzzle is None

    def test_returns_lowest_due_day_offset(self, db_session, player, game_event):
        """TODAY_RETURNS_LOWEST_DUE_DAY_OFFSET — when multiple puzzles
        are due (e.g. user opened the app 4 days after the game and
        hasn't completed day-0), return the lowest-offset due one."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        # Backdate every puzzle's due_at to the past so both day-0
        # and day-3 are due simultaneously.
        for p in plan.puzzles:
            p.due_at = datetime.utcnow() - timedelta(days=10)
        db_session.commit()

        result = _call_today(player, db_session)
        assert result is not None
        assert result.today_puzzle is not None
        assert result.today_puzzle.day_offset == 0

    def test_skips_completed_plan(self, db_session, player, game_event):
        """TODAY_SKIPS_COMPLETED_PLAN — status='completed' plan is not surfaced."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        plan.status = STATUS_COMPLETED
        db_session.commit()

        result = _call_today(player, db_session)
        assert result is None

    def test_returns_most_recent_active_plan(self, db_session, player):
        """TODAY_RETURNS_MOST_RECENT_ACTIVE_PLAN — two active plans → most recent by created_at."""
        # Two distinct GameEvents (UNIQUE constraint on (player, event))
        ev_old = GameEvent(
            player_id=player.id,
            pgn='[Result "0-1"]\n\n1. e4 e5 0-1',
            result="loss",
            accuracy=0.5,
            weaknesses_json="{}",
        )
        ev_new = GameEvent(
            player_id=player.id,
            pgn='[Result "0-1"]\n\n1. d4 d5 0-1',
            result="loss",
            accuracy=0.4,
            weaknesses_json="{}",
        )
        db_session.add(ev_old)
        db_session.add(ev_new)
        db_session.commit()

        plan_old = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=ev_old.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        plan_new = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=ev_new.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )
        # Bias created_at so the comparison isn't ambiguous within a
        # single-microsecond test window.
        plan_old.created_at = datetime.utcnow() - timedelta(hours=2)
        plan_new.created_at = datetime.utcnow()
        db_session.commit()

        result = _call_today(player, db_session)
        assert result is not None
        assert result.plan_id == plan_new.id

    def test_response_shape(self, db_session, player, game_event):
        """TODAY_RESPONSE_SHAPE — total_days=3, theme='generic', verdict='' in phase 1."""
        generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
        )

        result = _call_today(player, db_session)
        assert result is not None
        assert result.total_days == 3
        assert result.theme == "generic"
        assert result.verdict == ""
        assert isinstance(result.plan_id, str) and len(result.plan_id) > 0
