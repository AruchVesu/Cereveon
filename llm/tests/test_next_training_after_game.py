"""
Tests for GET /next-training/{player_id} — post-game recommendation behaviour.

These tests are deterministic: no live engine, no live database, no LLM calls.
All scheduler and server module dependencies are patched inline.

Covered concerns
----------------
1. Response schema: topic, difficulty, format, expected_gain all present & typed.
2. Scheduler integration: CurriculumScheduler.next_task() is called with
   the weaknesses list and the player's rating.
3. Default behaviour: unknown player → SkillState() defaults → scheduler sees
   the default rating (no crash).
4. Field value bounds: difficulty in [0, 1], expected_gain >= 0.
5. Format enum: format is one of the four documented values.
6. Post-game integration: recommendation changes when weakness is "tactics" vs
   "endgame" vs "strategy" (choose_task routing verified end-to-end).
7. AST guard: get_current_player is the dependency on next_training().
   (Updated from verify_api_key — see AUT-01 in test_security_authz.py.
   The endpoint now authenticates the JWT-bound player and validates that
   the path player_id matches; verify_api_key alone was insufficient
   because the API key is shared across all clients.)
8. AST guard: the route method is GET (not POST).
9. Schema separation: neither exercise_type nor payload appear in the response
   (those belong to /curriculum/next — kept as a regression guard).

Pinned invariants
-----------------
 1. SCHEMA_ALL_REQUIRED_FIELDS_PRESENT
 2. SCHEMA_TOPIC_IS_STRING
 3. SCHEMA_DIFFICULTY_IS_NUMERIC
 4. SCHEMA_FORMAT_IS_STRING
 5. SCHEMA_EXPECTED_GAIN_IS_NUMERIC
 6. SCHEMA_NO_EXERCISE_TYPE_FIELD
 7. SCHEMA_NO_PAYLOAD_FIELD
 8. BOUNDS_DIFFICULTY_GE_ZERO
 9. BOUNDS_DIFFICULTY_LE_ONE
10. BOUNDS_EXPECTED_GAIN_GE_ZERO
11. FORMAT_IS_VALID_ENUM_VALUE
12. SCHEDULER_RECEIVES_WEAKNESSES
13. SCHEDULER_RECEIVES_RATING
14. UNKNOWN_PLAYER_USES_DEFAULT_SKILL_STATE
15. TACTICS_WEAKNESS_RETURNS_PUZZLE_FORMAT
16. ENDGAME_WEAKNESS_RETURNS_DRILL_FORMAT
17. STRATEGY_WEAKNESS_RETURNS_EXPLANATION_FORMAT
18. EMPTY_WEAKNESSES_RETURNS_GENERAL_PLAY
19. AST_NEXT_TRAINING_HAS_GET_CURRENT_PLAYER  (was AST_*_VERIFY_API_KEY pre-AUT-01)
20. AST_NEXT_TRAINING_IS_GET_METHOD
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from llm.seca.curriculum.scheduler import CurriculumScheduler
from llm.seca.curriculum.task_selector import choose_task
from llm.seca.curriculum.types import TrainingTask, Weakness

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_FORMATS = {"puzzle", "drill", "game", "explanation"}
_REQUIRED_FIELDS = {"topic", "difficulty", "format", "expected_gain"}

_SERVER_PY = Path(__file__).resolve().parent.parent / "server.py"


def _call_next_training(
    monkeypatch,
    *,
    player_id: str = "test-player",
    task: TrainingTask | None = None,
):
    """Call server.next_training() with a controlled scheduler.

    After AUT-01 the endpoint requires a JWT-derived `player` instead of an
    API key, AND validates that the path player_id matches str(player.id).
    For these schema/scheduler tests we pin the authenticated player to
    have the same id as the path so we exercise the legitimate code path.
    Cross-tenant rejection (path != player.id → 403) is covered separately
    in test_security_authz.py::TestAut01NextTrainingCrossTenant.
    """
    import llm.server as server_module

    if task is None:
        task = TrainingTask(
            topic="tactics",
            difficulty=0.6,
            format="puzzle",
            expected_gain=0.56,
        )

    recorded: dict = {}

    class _CaptureScheduler:
        def next_task(self, weaknesses, rating):
            recorded["weaknesses"] = weaknesses
            recorded["rating"] = rating
            return task

    monkeypatch.setattr(server_module, "scheduler", _CaptureScheduler())
    fake_player = SimpleNamespace(id=player_id, rating=1200.0, confidence=0.5)
    result = server_module.next_training(player_id=player_id, player=fake_player)
    return result, recorded


# ---------------------------------------------------------------------------
# 1–7  Response schema
# ---------------------------------------------------------------------------


class TestNextTrainingResponseSchema:
    """SCHEMA_* — all required fields present with correct types."""

    def test_all_required_fields_present(self, monkeypatch):
        """SCHEMA_ALL_REQUIRED_FIELDS_PRESENT"""
        result, _ = _call_next_training(monkeypatch)
        missing = _REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Response missing required fields: {missing}"

    def test_topic_is_string(self, monkeypatch):
        """SCHEMA_TOPIC_IS_STRING"""
        result, _ = _call_next_training(monkeypatch)
        assert isinstance(result["topic"], str), (
            f"topic must be str, got {type(result['topic']).__name__}"
        )

    def test_difficulty_is_numeric(self, monkeypatch):
        """SCHEMA_DIFFICULTY_IS_NUMERIC"""
        result, _ = _call_next_training(monkeypatch)
        assert isinstance(result["difficulty"], (int, float)), (
            f"difficulty must be numeric, got {type(result['difficulty']).__name__}"
        )

    def test_format_is_string(self, monkeypatch):
        """SCHEMA_FORMAT_IS_STRING"""
        result, _ = _call_next_training(monkeypatch)
        assert isinstance(result["format"], str), (
            f"format must be str, got {type(result['format']).__name__}"
        )

    def test_expected_gain_is_numeric(self, monkeypatch):
        """SCHEMA_EXPECTED_GAIN_IS_NUMERIC"""
        result, _ = _call_next_training(monkeypatch)
        assert isinstance(result["expected_gain"], (int, float)), (
            f"expected_gain must be numeric, got {type(result['expected_gain']).__name__}"
        )

    def test_no_exercise_type_field(self, monkeypatch):
        """SCHEMA_NO_EXERCISE_TYPE_FIELD — belongs to /curriculum/next, not here."""
        result, _ = _call_next_training(monkeypatch)
        assert "exercise_type" not in result, (
            "exercise_type must not appear in /next-training response; "
            "it belongs to /curriculum/next."
        )

    def test_no_payload_field(self, monkeypatch):
        """SCHEMA_NO_PAYLOAD_FIELD — belongs to /curriculum/next, not here."""
        result, _ = _call_next_training(monkeypatch)
        assert "payload" not in result, (
            "payload must not appear in /next-training response; "
            "it belongs to /curriculum/next."
        )


# ---------------------------------------------------------------------------
# 8–11  Value bounds and format enum
# ---------------------------------------------------------------------------


class TestNextTrainingFieldBounds:
    """BOUNDS_* and FORMAT_* — value-level contracts."""

    def test_difficulty_is_ge_zero(self, monkeypatch):
        """BOUNDS_DIFFICULTY_GE_ZERO"""
        result, _ = _call_next_training(
            monkeypatch,
            task=TrainingTask(topic="tactics", difficulty=0.1, format="puzzle", expected_gain=0.1),
        )
        assert result["difficulty"] >= 0, (
            f"difficulty must be >= 0, got {result['difficulty']}"
        )

    def test_difficulty_is_le_one(self, monkeypatch):
        """BOUNDS_DIFFICULTY_LE_ONE"""
        result, _ = _call_next_training(
            monkeypatch,
            task=TrainingTask(topic="endgame", difficulty=0.95, format="drill", expected_gain=0.4),
        )
        assert result["difficulty"] <= 1.0, (
            f"difficulty must be <= 1.0, got {result['difficulty']}"
        )

    def test_expected_gain_is_non_negative(self, monkeypatch):
        """BOUNDS_EXPECTED_GAIN_GE_ZERO"""
        result, _ = _call_next_training(
            monkeypatch,
            task=TrainingTask(topic="tactics", difficulty=0.5, format="puzzle", expected_gain=0.0),
        )
        assert result["expected_gain"] >= 0, (
            f"expected_gain must be >= 0, got {result['expected_gain']}"
        )

    def test_format_is_valid_enum_value(self, monkeypatch):
        """FORMAT_IS_VALID_ENUM_VALUE"""
        for fmt in _VALID_FORMATS:
            result, _ = _call_next_training(
                monkeypatch,
                task=TrainingTask(topic="test", difficulty=0.5, format=fmt, expected_gain=0.5),
            )
            assert result["format"] in _VALID_FORMATS, (
                f"format={result['format']!r} not in {_VALID_FORMATS}"
            )


# ---------------------------------------------------------------------------
# 12–14  Scheduler integration
# ---------------------------------------------------------------------------


class TestNextTrainingSchedulerIntegration:
    """SCHEDULER_* — verify the scheduler is called correctly."""

    def test_scheduler_receives_weaknesses_list(self, monkeypatch):
        """SCHEDULER_RECEIVES_WEAKNESSES — next_task is called with a list."""
        _, recorded = _call_next_training(monkeypatch)
        assert "weaknesses" in recorded, "Scheduler was not called"
        assert isinstance(recorded["weaknesses"], list), (
            f"Scheduler expected a list of weaknesses, got {type(recorded['weaknesses'])}"
        )

    def test_scheduler_receives_player_rating(self, monkeypatch):
        """SCHEDULER_RECEIVES_RATING — next_task receives the player's rating."""
        _, recorded = _call_next_training(monkeypatch)
        assert "rating" in recorded, "Scheduler was not called with rating"
        assert isinstance(recorded["rating"], (int, float)), (
            f"rating must be numeric, got {type(recorded['rating'])}"
        )

    def test_unknown_player_uses_default_skill_state(self, monkeypatch):
        """UNKNOWN_PLAYER_USES_DEFAULT_SKILL_STATE — no crash for unknown player_id."""
        # player_id "does-not-exist" is not in player_skill_memory,
        # so the handler must fall back to SkillState() defaults.
        result, recorded = _call_next_training(monkeypatch, player_id="does-not-exist")
        assert "topic" in result, "Handler must not crash for unknown player_id"
        assert isinstance(recorded.get("rating"), (int, float)), (
            "Default rating must be numeric"
        )


# ---------------------------------------------------------------------------
# 15–18  Post-game integration (choose_task routing)
# ---------------------------------------------------------------------------


class TestNextTrainingPostGameFlow:
    """
    End-to-end routing through choose_task — verifies that the task_selector
    maps each weakness name to the correct format string.
    """

    def test_tactics_weakness_returns_puzzle_format(self):
        """TACTICS_WEAKNESS_RETURNS_PUZZLE_FORMAT"""
        weakness = Weakness(name="tactics", severity=0.7, confidence=0.9)
        task = choose_task(weakness, rating=1200.0)
        assert task.format == "puzzle", (
            f"tactics weakness must map to 'puzzle' format, got {task.format!r}"
        )

    def test_endgame_weakness_returns_drill_format(self):
        """ENDGAME_WEAKNESS_RETURNS_DRILL_FORMAT"""
        weakness = Weakness(name="endgame", severity=0.5, confidence=0.8)
        task = choose_task(weakness, rating=1200.0)
        assert task.format == "drill", (
            f"endgame weakness must map to 'drill' format, got {task.format!r}"
        )

    def test_strategy_weakness_returns_explanation_format(self):
        """STRATEGY_WEAKNESS_RETURNS_EXPLANATION_FORMAT"""
        weakness = Weakness(name="strategy", severity=0.4, confidence=0.7)
        task = choose_task(weakness, rating=1200.0)
        assert task.format == "explanation", (
            f"strategy weakness must map to 'explanation' format, got {task.format!r}"
        )

    def test_empty_weaknesses_returns_general_play(self):
        """EMPTY_WEAKNESSES_RETURNS_GENERAL_PLAY"""
        scheduler = CurriculumScheduler()
        task = scheduler.next_task(weaknesses=[], rating=1200.0)
        assert task.topic == "general_play", (
            f"No weaknesses must produce 'general_play' topic, got {task.topic!r}"
        )
        assert task.format == "game", (
            f"No weaknesses must produce 'game' format, got {task.format!r}"
        )


# ---------------------------------------------------------------------------
# 19–20  AST guards
# ---------------------------------------------------------------------------


def _parse_server() -> ast.Module:
    return ast.parse(_SERVER_PY.read_text(encoding="utf-8"))


def _get_function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _depends_on(func_def, target: str) -> bool:
    for default in func_def.args.defaults + func_def.args.kw_defaults:
        if default is None:
            continue
        if not isinstance(default, ast.Call):
            continue
        if isinstance(default.func, ast.Name) and default.func.id == "Depends":
            for arg in default.args:
                if isinstance(arg, ast.Name) and arg.id == target:
                    return True
    return False


class TestNextTrainingAstGuards:
    """AST inspection of server.py — authentication and HTTP method invariants."""

    def test_next_training_has_get_current_player_dependency(self):
        """AST_NEXT_TRAINING_HAS_GET_CURRENT_PLAYER

        Updated from the original verify_api_key check after AUT-01: the
        endpoint must authenticate the JWT-bound player so the cross-tenant
        check (path player_id == str(player.id)) is enforceable.  The
        previous verify_api_key dependency was insufficient because the
        API key is shared across all clients and gave any caller access
        to any player_id."""
        tree = _parse_server()
        func = _get_function(tree, "next_training")
        assert func is not None, "next_training() not found in server.py"
        assert _depends_on(func, "get_current_player"), (
            "GET /next-training/{player_id} must have Depends(get_current_player). "
            "The endpoint returns player-specific training data; without a JWT-bound "
            "player there is no identity to cross-check the path parameter against."
        )

    def test_next_training_decorator_is_get_method(self):
        """AST_NEXT_TRAINING_IS_GET_METHOD — route must be GET, not POST."""
        tree = _parse_server()
        func = _get_function(tree, "next_training")
        assert func is not None, "next_training() not found in server.py"

        # Walk decorators looking for app.get(...)
        found_get = False
        for decorator in func.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            fn = decorator.func
            # Match app.get or router.get
            if isinstance(fn, ast.Attribute) and fn.attr == "get":
                found_get = True
                break
        assert found_get, (
            "next_training() must be decorated with @app.get(...), not @app.post(...). "
            "GET is the documented HTTP method for /next-training/{player_id}."
        )
