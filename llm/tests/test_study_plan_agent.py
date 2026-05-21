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


# ---------------------------------------------------------------------------
# Tiny in-test LLM helpers
# ---------------------------------------------------------------------------
#
# The shipped ``FakeLLM`` modes don't include "return structured JSON"
# shapes, so phase 2's verdict path needs its own scripted fakes.
# Keeping them inline here (rather than extending FakeLLM with more
# modes) avoids polluting the production fixture with study-plan-
# specific behaviour.


class _ScriptedLLM:
    """A BaseLLM-shaped fake that returns a queued sequence of strings.

    Each call to ``generate`` pops the next entry; if the queue is
    exhausted, the LAST entry is returned again (so a single-entry
    queue acts as a constant generator).  Subclassing BaseLLM is not
    required at the Python level because verdict.generate_verdict
    only uses ``.generate(prompt)`` — duck typing is enough."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if len(self.calls) <= len(self.responses):
            return self.responses[len(self.calls) - 1]
        return self.responses[-1]


class _RaisingLLM:
    """A BaseLLM-shaped fake whose ``generate`` always raises.  Used to
    exercise the LLM-unreachable fallback path in verdict.py."""

    def generate(self, prompt: str) -> str:
        raise RuntimeError("simulated LLM provider outage")


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


class TestVerdictGeneration:
    """LLM verdict path (phase 2).  The agent calls
    ``generate_verdict(...)`` after committing the plan + 3 puzzle
    rows; the result populates ``plan.theme`` + ``plan.verdict``.
    Failure paths leave the plan at the phase-1 stub values so the
    schedule + dedup contract is preserved even when DeepSeek is
    down.

    All tests inject a scripted in-test fake LLM so no real DeepSeek
    calls happen during the suite."""

    _CLEAN_VERDICT = (
        "Bringing the king toward the centre with pieces still on the "
        "board exposes it to a quick attack; the resulting tempo loss "
        "let the opponent build pressure faster than it could be defended."
    )

    def _clean_json(self, theme: str = "king_safety") -> str:
        return f'{{"theme": "{theme}", "verdict": "{self._CLEAN_VERDICT}"}}'

    def test_verdict_happy_path(self, db_session, player, game_event):
        """VERDICT_HAPPY_PATH — LLM returns a valid JSON; plan.theme +
        plan.verdict are populated.  Day-0 puzzle FEN is unchanged
        (the verdict step doesn't touch puzzles)."""
        llm = _ScriptedLLM([self._clean_json()])

        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
        )

        assert plan is not None
        assert plan.theme == "king_safety"
        assert plan.verdict == self._CLEAN_VERDICT
        # Puzzles are still the phase-1 stub (day-3/day-7 unchanged).
        assert len(plan.puzzles) == 3

    def test_verdict_json_parse_failure_falls_back(
        self, db_session, player, game_event
    ):
        """VERDICT_JSON_PARSE_FAILURE — LLM returns garbage; plan keeps
        the phase-1 stub values.  ``("generic", "")`` is the documented
        fallback shape so the Android Home card hides the coach-note
        line cleanly."""
        # Two responses because verdict.py retries once on parse failure.
        llm = _ScriptedLLM(["not json", "still not json"])

        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
        )

        assert plan is not None
        assert plan.theme == "generic"
        assert plan.verdict == ""

    def test_verdict_unknown_theme_collapses_to_generic(
        self, db_session, player, game_event
    ):
        """VERDICT_UNKNOWN_THEME — LLM returns a valid JSON but the
        theme tag is not in ``THEME_VOCABULARY``.  Theme collapses to
        ``"generic"``; verdict text is preserved because the verdict
        itself passed every validator."""
        bad_theme_json = (
            f'{{"theme": "made_up_tag_xyz", "verdict": "{self._CLEAN_VERDICT}"}}'
        )
        llm = _ScriptedLLM([bad_theme_json])

        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
        )

        assert plan is not None
        assert plan.theme == "generic"
        assert plan.verdict == self._CLEAN_VERDICT

    def test_verdict_validator_retry_succeeds(
        self, db_session, player, game_event
    ):
        """VERDICT_VALIDATOR_RETRY — first LLM response trips the
        Mode-2 negative validator (mentions the engine), the retry
        produces a clean verdict, the clean one wins."""
        forbidden_json = (
            '{"theme": "king_safety", '
            '"verdict": "Stockfish evaluates this position as losing for you."}'
        )
        llm = _ScriptedLLM([forbidden_json, self._clean_json()])

        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
        )

        assert plan is not None
        assert plan.theme == "king_safety"
        assert plan.verdict == self._CLEAN_VERDICT
        # Verdict module called the LLM exactly twice (one retry).
        assert len(llm.calls) == 2

    def test_verdict_validator_double_fail_falls_back(
        self, db_session, player, game_event
    ):
        """VERDICT_VALIDATOR_DOUBLE_FAIL — both LLM responses trip the
        validators; plan falls back to ``("generic", "")``.  The
        prompt-engineering effort to coax a clean rewrite gave up
        after one retry."""
        forbidden_json = (
            '{"theme": "king_safety", '
            '"verdict": "Stockfish evaluates this position as losing."}'
        )
        llm = _ScriptedLLM([forbidden_json, forbidden_json])

        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
        )

        assert plan is not None
        assert plan.theme == "generic"
        assert plan.verdict == ""

    def test_verdict_llm_unreachable_falls_back(
        self, db_session, player, game_event
    ):
        """VERDICT_LLM_TIMEOUT_FALLBACK — every ``llm.generate`` call
        raises (provider down).  Plan stays at the stub; no exception
        propagates out of the agent."""
        llm = _RaisingLLM()

        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
        )

        assert plan is not None
        assert plan.theme == "generic"
        assert plan.verdict == ""

    def test_verdict_skipped_when_llm_is_none(
        self, db_session, player, game_event
    ):
        """VERDICT_SKIPPED_WHEN_LLM_NONE — phase-1 callers that pass
        ``llm=None`` get the stub values without any LLM round-trip
        and without raising.  Pins the optional-injection contract
        so existing tests stay valid."""
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=None,
        )

        assert plan is not None
        assert plan.theme == "generic"
        assert plan.verdict == ""


class TestVerdictBranchCoverage:
    """Direct unit tests for the verdict module's narrower failure
    paths.  These call ``generate_verdict`` directly with rigged
    inputs to exercise branches the higher-level ``generate_plan``
    path doesn't hit naturally.

    The function never raises — every test asserts the documented
    fallback shape ``("generic", "")`` or a valid ``(theme, verdict)``."""

    _CLEAN_VERDICT = (
        "Bringing the king toward the centre with pieces still on the "
        "board exposes it to a quick attack; the resulting tempo loss "
        "let the opponent build pressure faster than it could be defended."
    )

    def test_non_dict_json_falls_back(self):
        """VERDICT_NON_DICT_JSON — LLM returns valid JSON but it's an
        array, not an object.  Falls back after the single retry."""
        from llm.seca.coach.study_plan.verdict import generate_verdict

        llm = _ScriptedLLM(['["not", "a", "dict"]', '["still not"]'])
        result = generate_verdict(
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            player_skill_hint="intermediate",
            llm=llm,
        )
        assert result == ("generic", "")
        # Both attempts were called (retry path).
        assert len(llm.calls) == 2

    def test_missing_theme_field_falls_back(self):
        """VERDICT_MISSING_THEME — LLM returns valid JSON but theme
        field is absent (or non-string)."""
        from llm.seca.coach.study_plan.verdict import generate_verdict

        json_without_theme = '{"verdict": "some content here that is plenty long."}'
        llm = _ScriptedLLM([json_without_theme, json_without_theme])
        result = generate_verdict(
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            player_skill_hint="intermediate",
            llm=llm,
        )
        assert result == ("generic", "")

    def test_empty_verdict_falls_back(self):
        """VERDICT_EMPTY_AFTER_STRIP — LLM returns valid JSON with
        verdict="" (or whitespace only).  After ``.strip()`` it's
        empty and the function falls back."""
        from llm.seca.coach.study_plan.verdict import generate_verdict

        empty_verdict_json = '{"theme": "fork", "verdict": "   "}'
        llm = _ScriptedLLM([empty_verdict_json, empty_verdict_json])
        result = generate_verdict(
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            player_skill_hint="intermediate",
            llm=llm,
        )
        assert result == ("generic", "")

    def test_oversized_verdict_falls_back(self):
        """VERDICT_OVERSIZED — LLM returns a verdict over 500 chars.
        Falls back without surfacing the wall-of-text to the user
        (the UI's one-glance card can't render it anyway)."""
        from llm.seca.coach.study_plan.verdict import generate_verdict

        huge = "x" * 600
        json_huge = f'{{"theme": "fork", "verdict": "{huge}"}}'
        llm = _ScriptedLLM([json_huge, json_huge])
        result = generate_verdict(
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            player_skill_hint="intermediate",
            llm=llm,
        )
        assert result == ("generic", "")

    def test_code_fence_wrapper_is_stripped(self):
        """VERDICT_CODE_FENCE_STRIPPED — some models wrap JSON in a
        ```json ... ``` fence despite the prompt instruction; the
        parser strips the fence and accepts the inner JSON."""
        from llm.seca.coach.study_plan.verdict import generate_verdict

        wrapped = (
            "```json\n"
            f'{{"theme": "king_safety", "verdict": "{self._CLEAN_VERDICT}"}}\n'
            "```"
        )
        llm = _ScriptedLLM([wrapped])
        theme, verdict = generate_verdict(
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            player_skill_hint="intermediate",
            llm=llm,
        )
        assert theme == "king_safety"
        assert verdict == self._CLEAN_VERDICT

    def test_empty_llm_output_falls_back(self):
        """VERDICT_EMPTY_LLM_OUTPUT — LLM returns an empty string or
        whitespace.  The pre-JSON ``cleaned`` check short-circuits
        before json.loads is attempted."""
        from llm.seca.coach.study_plan.verdict import generate_verdict

        llm = _ScriptedLLM(["", "   "])
        result = generate_verdict(
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            player_skill_hint="intermediate",
            llm=llm,
        )
        assert result == ("generic", "")

    def test_retry_raise_falls_back(self):
        """VERDICT_RETRY_LLM_RAISES — first attempt succeeds at JSON
        parse but trips a validator; retry attempt raises (provider
        died between calls).  Falls back to ``("generic", "")``."""
        from llm.seca.coach.study_plan.verdict import generate_verdict

        forbidden = (
            '{"theme": "king_safety", '
            '"verdict": "Stockfish evaluates this position as losing."}'
        )

        class _FlakyLLM:
            """First call returns forbidden text; second call raises."""

            def __init__(self) -> None:
                self.call_count = 0

            def generate(self, prompt: str) -> str:
                self.call_count += 1
                if self.call_count == 1:
                    return forbidden
                raise RuntimeError("simulated mid-retry provider drop")

        llm = _FlakyLLM()
        result = generate_verdict(
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            player_skill_hint="intermediate",
            llm=llm,
        )
        assert result == ("generic", "")
        assert llm.call_count == 2


class TestPuzzleLibraryLoad:
    """``library.load_library`` reads YAML, validates every entry,
    and crashes on the FIRST failure with an identifying error
    message."""

    def _write_yaml(self, tmp_path, content: str, name: str = "p.yaml") -> None:
        (tmp_path / name).write_text(content, encoding="utf-8")

    def test_loads_shipped_corpus(self):
        """LIBRARY_LOAD_SHIPPED_CORPUS — the loader succeeds against
        the repo's seed corpus."""
        from llm.seca.coach.study_plan.library import load_library

        lib = load_library()
        total = sum(len(v) for v in lib.values())
        assert total >= 1

    def test_rejects_unknown_theme(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: bad_theme_x\n"
                "  theme: not_a_real_theme\n"
                "  difficulty: beginner\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "e7e8q"\n'
                '  description: "stub"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="unknown theme"):
            lib_module.load_library()

    def test_rejects_unknown_difficulty(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: bad_diff_x\n"
                "  theme: generic\n"
                "  difficulty: master\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "e7e8q"\n'
                '  description: "stub"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="unknown difficulty"):
            lib_module.load_library()

    def test_rejects_illegal_move(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: illegal_move_x\n"
                "  theme: generic\n"
                "  difficulty: beginner\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "a1a8"\n'
                '  description: "stub"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="not a legal move"):
            lib_module.load_library()

    def test_rejects_unparseable_fen(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: bad_fen_x\n"
                "  theme: generic\n"
                "  difficulty: beginner\n"
                '  fen: "this is not a fen at all"\n'
                '  expected_move_uci: "e2e4"\n'
                '  description: "stub"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError):
            lib_module.load_library()

    def test_rejects_duplicate_id(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: same_id\n"
                "  theme: generic\n"
                "  difficulty: beginner\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "e7e8q"\n'
                '  description: "first"\n'
                "- id: same_id\n"
                "  theme: generic\n"
                "  difficulty: beginner\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "e7e8q"\n'
                '  description: "second"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="duplicate puzzle id"):
            lib_module.load_library()

    def test_rejects_missing_field(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: missing_desc_x\n"
                "  theme: generic\n"
                "  difficulty: beginner\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "e7e8q"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="missing required field"):
            lib_module.load_library()

    def test_rejects_non_string_field(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: non_string_x\n"
                "  theme: 42\n"
                "  difficulty: beginner\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "e7e8q"\n'
                '  description: "stub"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="must be a string"):
            lib_module.load_library()

    def test_rejects_non_list_top_level(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content="wrong_shape:\n  - foo\n",
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="top-level must be a list"):
            lib_module.load_library()

    def test_rejects_non_dict_entry(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(tmp_path, content="- just_a_string\n")
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError, match="entry must be a dict"):
            lib_module.load_library()

    def test_empty_yaml_file_skipped(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(tmp_path, content="", name="empty.yaml")
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        lib = lib_module.load_library()
        assert all(v == [] for v in lib.values())

    def test_unparseable_uci_falls_back_to_validation_error(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        self._write_yaml(
            tmp_path,
            content=(
                "- id: bad_uci_x\n"
                "  theme: generic\n"
                "  difficulty: beginner\n"
                '  fen: "8/4P3/8/8/8/8/8/4K2k w - - 0 1"\n'
                '  expected_move_uci: "???"\n'
                '  description: "stub"\n'
            ),
        )
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", tmp_path)
        with pytest.raises(lib_module.LibraryValidationError):
            lib_module.load_library()

    def test_missing_library_dir_returns_empty(self, tmp_path, monkeypatch):
        from llm.seca.coach.study_plan import library as lib_module

        missing = tmp_path / "does_not_exist"
        monkeypatch.setattr(lib_module, "_LIBRARY_DIR", missing)
        lib = lib_module.load_library()
        assert lib == {theme: [] for theme in lib_module.THEME_VOCABULARY}


class TestPuzzleLibraryPicker:
    """``library.pick_two_puzzles`` — deterministic per plan_id."""

    def _puzzle(self, pid: str, theme: str, difficulty: str = "intermediate"):
        from llm.seca.coach.study_plan.library import LibraryPuzzle

        return LibraryPuzzle(
            id=pid,
            theme=theme,
            difficulty=difficulty,
            fen="8/4P3/8/8/8/8/8/4K2k w - - 0 1",
            expected_move_uci="e7e8q",
            description="stub",
        )

    def test_returns_two_distinct(self):
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        library = {
            "fork": [self._puzzle("a", "fork"), self._puzzle("b", "fork"), self._puzzle("c", "fork")],
            "generic": [],
        }
        d3, d7 = pick_two_puzzles(library, "fork", "intermediate", "plan-1")
        assert d3 is not None and d7 is not None
        assert d3.id != d7.id

    def test_deterministic_per_plan_id(self):
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        library = {"fork": [self._puzzle(c, "fork") for c in "abcdef"], "generic": []}
        first = pick_two_puzzles(library, "fork", "intermediate", "plan-42")
        second = pick_two_puzzles(library, "fork", "intermediate", "plan-42")
        assert (first[0].id, first[1].id) == (second[0].id, second[1].id)

    def test_different_plan_ids_can_pick_different_puzzles(self):
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        library = {"fork": [self._puzzle(c, "fork") for c in "abcdefgh"], "generic": []}
        pairs = set()
        for plan_id in [f"plan-{i}" for i in range(50)]:
            d3, d7 = pick_two_puzzles(library, "fork", "intermediate", plan_id)
            pairs.add((d3.id, d7.id))
        assert len(pairs) >= 3

    def test_falls_back_to_generic(self):
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        library = {
            "fork": [],
            "generic": [self._puzzle("g1", "generic"), self._puzzle("g2", "generic")],
        }
        d3, d7 = pick_two_puzzles(library, "fork", "intermediate", "plan-1")
        assert d3.theme == "generic" and d7.theme == "generic"

    def test_returns_none_when_library_empty(self):
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        result = pick_two_puzzles({"fork": [], "generic": []}, "fork", "intermediate", "plan-1")
        assert result == (None, None)

    def test_single_puzzle_returned_twice(self):
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        only = self._puzzle("solo", "fork")
        d3, d7 = pick_two_puzzles(
            {"fork": [only], "generic": []}, "fork", "intermediate", "plan-1"
        )
        assert d3 is only and d7 is only

    def test_skill_filter_biases_band(self):
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        library = {
            "fork": [
                self._puzzle("b1", "fork", "beginner"),
                self._puzzle("b2", "fork", "beginner"),
                self._puzzle("a1", "fork", "advanced"),
                self._puzzle("a2", "fork", "advanced"),
            ],
            "generic": [],
        }
        picked_ids: set[str] = set()
        for plan_id in [f"p-{i}" for i in range(30)]:
            d3, d7 = pick_two_puzzles(library, "fork", "beginner", plan_id)
            picked_ids.add(d3.id)
            picked_ids.add(d7.id)
        assert "a1" not in picked_ids
        assert "a2" not in picked_ids

    def test_unknown_skill_hint_falls_through(self):
        """When skill_hint isn't a known band (defensive), the filter
        doesn't crash — it just returns the whole candidate pool."""
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        library = {
            "fork": [self._puzzle("a", "fork"), self._puzzle("b", "fork")],
            "generic": [],
        }
        d3, d7 = pick_two_puzzles(library, "fork", "unknown_band", "plan-1")
        assert d3 is not None and d7 is not None

    def test_skill_filter_falls_through_when_empty(self):
        """When the skill filter would empty the candidate pool, fall
        through to the unfiltered pool rather than returning (None, None)."""
        from llm.seca.coach.study_plan.library import pick_two_puzzles

        library = {
            "fork": [self._puzzle("a1", "fork", "advanced"), self._puzzle("a2", "fork", "advanced")],
            "generic": [],
        }
        # Beginner skill_hint + advanced-only puzzles: filter would
        # empty (band gap is 2).  Should fall through to the full pool.
        d3, d7 = pick_two_puzzles(library, "fork", "beginner", "plan-1")
        assert d3 is not None and d7 is not None


class TestAgentLibraryIntegration:
    """``generate_plan`` end-to-end with verdict + library wired."""

    _CLEAN_VERDICT = (
        "Bringing the king toward the centre with pieces still on the "
        "board exposes it to a quick attack; the resulting tempo loss "
        "let the opponent build pressure faster than it could be defended."
    )

    def _puzzle(self, pid, theme, fen, move="e2e4", difficulty="intermediate"):
        from llm.seca.coach.study_plan.library import LibraryPuzzle

        return LibraryPuzzle(
            id=pid,
            theme=theme,
            difficulty=difficulty,
            fen=fen,
            expected_move_uci=move,
            description="stub",
        )

    def test_phase_3_replaces_day_3_and_day_7(self, db_session, player, game_event):
        from llm.seca.coach.study_plan.models import (
            PUZZLE_SOURCE_LIBRARY,
            PUZZLE_SOURCE_ORIGINAL,
        )

        json_clean = f'{{"theme": "king_safety", "verdict": "{self._CLEAN_VERDICT}"}}'
        llm = _ScriptedLLM([json_clean])
        library = {
            "king_safety": [
                self._puzzle(
                    "ks_a", "king_safety",
                    "8/4P3/8/8/8/8/8/4K2k w - - 0 1", "e7e8q",
                ),
                self._puzzle(
                    "ks_b", "king_safety",
                    "4k3/8/8/8/3b4/4Q3/8/4K3 w - - 0 1", "e3d4",
                ),
            ],
            "generic": [],
        }

        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
            library=library,
        )

        by_offset = {p.day_offset: p for p in plan.puzzles}
        assert by_offset[0].fen == _MISTAKE_FEN
        assert by_offset[0].source_type == PUZZLE_SOURCE_ORIGINAL
        for offset in (3, 7):
            assert by_offset[offset].source_type == PUZZLE_SOURCE_LIBRARY
            assert by_offset[offset].fen != _MISTAKE_FEN
        assert by_offset[3].fen != by_offset[7].fen

    def test_phase_3_falls_back_when_library_empty(self, db_session, player, game_event):
        from llm.seca.coach.study_plan.models import PUZZLE_SOURCE_ORIGINAL

        llm = _ScriptedLLM(
            [f'{{"theme": "king_safety", "verdict": "{self._CLEAN_VERDICT}"}}']
        )
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=llm,
            library={"king_safety": [], "generic": []},
        )
        for puzzle in plan.puzzles:
            assert puzzle.fen == _MISTAKE_FEN
            assert puzzle.source_type == PUZZLE_SOURCE_ORIGINAL

    def test_phase_3_skipped_when_llm_is_none(self, db_session, player, game_event):
        from llm.seca.coach.study_plan.models import PUZZLE_SOURCE_ORIGINAL

        library = {
            "generic": [
                self._puzzle(
                    "gn_test", "generic",
                    "8/4P3/8/8/8/8/8/4K2k w - - 0 1", "e7e8q",
                ),
            ]
        }
        plan = generate_plan(
            db=db_session,
            player_id=player.id,
            source_event_id=game_event.id,
            mistake_fen=_MISTAKE_FEN,
            played_uci=_PLAYED_UCI,
            llm=None,
            library=library,
        )
        for puzzle in plan.puzzles:
            assert puzzle.fen == _MISTAKE_FEN
            assert puzzle.source_type == PUZZLE_SOURCE_ORIGINAL


class TestSkillHintForRating:
    """``skill_hint_for_rating`` maps a Player.rating value to one of
    three bands the LLM prompt uses to shape vocabulary.  Bands pinned
    so a future tuning pass changes one place, not 30 string asserts."""

    def test_below_1200_is_beginner(self):
        from llm.seca.coach.study_plan.verdict import skill_hint_for_rating

        assert skill_hint_for_rating(0.0) == "beginner"
        assert skill_hint_for_rating(800.0) == "beginner"
        assert skill_hint_for_rating(1199.9) == "beginner"

    def test_1200_to_1800_is_intermediate(self):
        from llm.seca.coach.study_plan.verdict import skill_hint_for_rating

        assert skill_hint_for_rating(1200.0) == "intermediate"
        assert skill_hint_for_rating(1500.0) == "intermediate"
        assert skill_hint_for_rating(1799.9) == "intermediate"

    def test_1800_and_above_is_advanced(self):
        from llm.seca.coach.study_plan.verdict import skill_hint_for_rating

        assert skill_hint_for_rating(1800.0) == "advanced"
        assert skill_hint_for_rating(2200.0) == "advanced"
        assert skill_hint_for_rating(2800.0) == "advanced"


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
        session.

        Passes an explicit ``llm=`` so the wrapper doesn't try to
        construct a real ``DeepseekLLM`` (which would attempt a
        network call during the unit test).  A scripted in-test fake
        returning a valid JSON-shaped verdict exercises the phase-2
        verdict path end-to-end at the same time."""
        from llm.seca.auth import router as auth_router_module
        from llm.seca.coach.study_plan.agent import generate_plan_async

        original_close = db_session.close
        db_session.close = lambda: None
        monkeypatch.setattr(auth_router_module, "SessionLocal", lambda: db_session)

        fake_llm = _ScriptedLLM(
            [
                '{"theme": "king_safety", '
                '"verdict": "Bringing the king toward the centre with pieces still '
                "on the board exposes it to a quick attack; the resulting tempo "
                'loss let the opponent build pressure faster than it could be defended."}'
            ]
        )

        try:
            generate_plan_async(
                player_id=player.id,
                source_event_id=game_event.id,
                mistake_fen=_MISTAKE_FEN,
                played_uci=_PLAYED_UCI,
                llm=fake_llm,
            )
        finally:
            db_session.close = original_close

        plans = db_session.query(MistakeStudyPlan).all()
        puzzles = db_session.query(MistakeStudyPuzzle).all()
        assert len(plans) == 1
        assert len(puzzles) == 3
        # Verdict path also fired end-to-end through the async wrapper.
        assert plans[0].theme == "king_safety"
        assert plans[0].verdict.startswith("Bringing the king")

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
