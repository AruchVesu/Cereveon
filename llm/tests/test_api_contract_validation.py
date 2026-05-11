"""
API contract validation tests.

Verifies that the backend endpoint response structures match the schemas
documented in docs/API_CONTRACTS.md.  Tests are deterministic, require no
live engine or database, and fail CI if any field is missing or has the wrong
type.

Covered endpoints:
  - POST /engine/eval  (server.py — migrated from host_app.py in 2026-05-12)
  - GET  /next-training/{player_id}  (server.py)
  - POST /game/finish  (llm/seca/events/router.py)

Documented mismatches captured as dedicated test classes:
  - TestCoachEndpointMissing     — /coach does not exist
  - TestNextTrainingSchemaConflict — /next-training vs /curriculum/next
  - TestCoachExecutorHandlerGap  — PUZZLE / PLAN_UPDATE fall back to default
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_STR_OR_NONE = (str, type(None))


def _assert_str_or_none(value, field: str) -> None:
    assert isinstance(
        value, _REQUIRED_STR_OR_NONE
    ), f"{field} must be str | None, got {type(value).__name__}: {value!r}"


def _assert_int_or_none(value, field: str) -> None:
    assert isinstance(
        value, (int, type(None))
    ), f"{field} must be int | None, got {type(value).__name__}: {value!r}"


def _assert_str(value, field: str) -> None:
    assert isinstance(value, str), f"{field} must be str, got {type(value).__name__}: {value!r}"


def _assert_float(value, field: str) -> None:
    assert isinstance(
        value, (int, float)
    ), f"{field} must be numeric, got {type(value).__name__}: {value!r}"


def _assert_dict(value, field: str) -> None:
    assert isinstance(value, dict), f"{field} must be dict, got {type(value).__name__}: {value!r}"


# ---------------------------------------------------------------------------
# 1. /engine/eval — POST (server.py)
# ---------------------------------------------------------------------------
#
# Migrated from host_app.py in the host_app retirement pass.  The server.py
# contract is intentionally narrower than host_app's was:
#   - POST only (no GET variant — nothing in-tree calls GET).
#   - Body is just ``{"fen": str}``; the historical ``moves`` /
#     ``movetime_ms`` / ``nodes`` fields are gone.
#   - Response is ``{"score": int|None, "best_move": str|None, "source": str}``.
#     No ``_metrics`` field, no ``cache``/``book`` source values — the new
#     route doesn't go through EliteEngineService.  ``source`` is
#     "engine" on the happy path, "unavailable" when the engine pool is
#     down (matches the Android client's ``engineAvailable=false`` branch).


class _FakeEngine:
    """Minimal stand-in for ``chess.engine.SimpleEngine`` — enough surface
    for ``engine.analyse(board, limit)`` to return the shape the
    /engine/eval handler reads."""

    def __init__(self, score_cp: int | None, best_move_uci: str | None):
        self._score_cp = score_cp
        self._best_move_uci = best_move_uci

    def analyse(self, _board, _limit):
        import chess.engine  # noqa: PLC0415

        info: dict = {}
        if self._score_cp is not None:
            info["score"] = chess.engine.PovScore(
                chess.engine.Cp(self._score_cp), chess.WHITE
            )
        if self._best_move_uci is not None:
            info["pv"] = [chess.Move.from_uci(self._best_move_uci)]
        return info


class _FakeEnginePool:
    """Stand-in for ``StockfishEnginePool`` exposing the two private
    attributes the /engine/eval handler reaches into: ``_engines``
    (Queue-like with ``.get``) and ``_release_engine`` (no-op on
    release)."""

    def __init__(self, engine: _FakeEngine | None):
        self._engine = engine

        class _Settings:
            queue_timeout_ms = 1000

        self.settings = _Settings()

        class _Queue:
            def __init__(self, eng):
                self._eng = eng

            def get(self, timeout=None):  # noqa: ARG002
                if self._eng is None:
                    import queue as _q  # noqa: PLC0415

                    raise _q.Empty()
                return self._eng

        self._engines = _Queue(engine)

    def _release_engine(self, _engine):
        pass


class TestEngineEvalContractSchema:
    """POST /engine/eval response schema validation.

    Calls the handler function directly (rather than via TestClient) so
    the test stays in-process and doesn't depend on the FastAPI lifespan
    booting a real Stockfish pool.  The rate limiter is bypassed by
    flipping ``limiter.enabled`` to False inside the fixture; the
    real handler decorator is preserved so production behaviour is
    unchanged.
    """

    def _run_engine_eval(self, monkeypatch, *, score=42, best_move="e2e4"):
        import llm.server as server_module

        fake_pool = _FakeEnginePool(_FakeEngine(score, best_move))
        monkeypatch.setattr(server_module, "engine_pool", fake_pool)
        monkeypatch.setattr(server_module.limiter, "enabled", False)

        # Direct call — bypasses the Depends() chain.  X-Api-Key
        # verification is server.py:verify_api_key (separately tested);
        # we pass _=None to skip the dependency injection.
        return server_module.engine_eval(
            req=server_module.EngineEvalRequest(
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            ),
            request=MagicMock(),
            _=None,
        )

    def test_response_has_score_field(self, monkeypatch):
        result = self._run_engine_eval(monkeypatch)
        assert "score" in result, "Response missing required field 'score'"

    def test_response_has_best_move_field(self, monkeypatch):
        result = self._run_engine_eval(monkeypatch)
        assert "best_move" in result, "Response missing required field 'best_move'"

    def test_response_has_source_field(self, monkeypatch):
        result = self._run_engine_eval(monkeypatch)
        assert "source" in result, "Response missing required field 'source'"

    def test_score_is_int_or_none(self, monkeypatch):
        """score must be int | None (centipawns from White perspective)."""
        result = self._run_engine_eval(monkeypatch, score=42)
        _assert_int_or_none(result["score"], "score")

    def test_best_move_is_str_or_none(self, monkeypatch):
        result = self._run_engine_eval(monkeypatch, best_move="e2e4")
        _assert_str_or_none(result["best_move"], "best_move")

    def test_score_sign_convention_positive_means_white_better(self, monkeypatch):
        result = self._run_engine_eval(monkeypatch, score=100)
        assert result["score"] == 100
        assert result["score"] > 0

    def test_score_sign_convention_negative_means_black_better(self, monkeypatch):
        result = self._run_engine_eval(monkeypatch, score=-80)
        assert result["score"] == -80
        assert result["score"] < 0

    def test_source_engine_on_happy_path(self, monkeypatch):
        """``source`` is "engine" when the pool returned a real eval."""
        result = self._run_engine_eval(monkeypatch)
        assert result["source"] == "engine"

    def test_engine_pool_unavailable_returns_unavailable_source(self, monkeypatch):
        """When ``engine_pool`` is None (boot failure), the handler
        returns a degraded shape with ``source="unavailable"`` rather
        than 500 — matches the Android client's ``engineAvailable=false``
        fallback branch in ChessViewModel.dispatchEngineEval."""
        import llm.server as server_module

        monkeypatch.setattr(server_module, "engine_pool", None)
        monkeypatch.setattr(server_module.limiter, "enabled", False)

        result = server_module.engine_eval(
            req=server_module.EngineEvalRequest(
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            ),
            request=MagicMock(),
            _=None,
        )
        assert result == {"score": None, "best_move": None, "source": "unavailable"}

    def test_queue_timeout_returns_unavailable_source(self, monkeypatch):
        """A queue.Empty from ``engine_pool._engines.get(timeout=...)``
        (pool exhausted under load) surfaces as ``source="unavailable"``
        with null score+best_move, same as the pool-is-None branch."""
        import llm.server as server_module

        empty_pool = _FakeEnginePool(engine=None)  # raises queue.Empty
        monkeypatch.setattr(server_module, "engine_pool", empty_pool)
        monkeypatch.setattr(server_module.limiter, "enabled", False)

        result = server_module.engine_eval(
            req=server_module.EngineEvalRequest(
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            ),
            request=MagicMock(),
            _=None,
        )
        assert result["source"] == "unavailable"
        assert result["score"] is None
        assert result["best_move"] is None


# ---------------------------------------------------------------------------
# 2. GET /next-training/{player_id} (server.py)
# ---------------------------------------------------------------------------

_NEXT_TRAINING_REQUIRED = {"topic", "difficulty", "format", "expected_gain"}


class TestNextTrainingContractSchema:
    """GET /next-training/{player_id} response schema validation."""

    def _call_next_training(self, monkeypatch, player_id="p1"):
        import llm.server as server_module
        from llm.seca.curriculum.types import TrainingTask

        fake_task = TrainingTask(
            topic="tactics",
            difficulty=0.6,
            format="puzzle",
            expected_gain=2.5,
        )

        class _FakeScheduler:
            def next_task(self, weaknesses, rating):
                return fake_task

        monkeypatch.setattr(server_module, "scheduler", _FakeScheduler())

        # Call handler directly.  After AUT-01 the dependency is
        # get_current_player (JWT-derived) and the handler enforces
        # path player_id == str(player.id).  Pin both to the same id
        # so the legitimate code path is exercised.
        from types import SimpleNamespace as _SN
        fake_player = _SN(id=player_id, rating=1200.0, confidence=0.5)
        return server_module.next_training(player_id=player_id, player=fake_player)

    def test_response_has_all_required_fields(self, monkeypatch):
        result = self._call_next_training(monkeypatch)
        missing = _NEXT_TRAINING_REQUIRED - set(result.keys())
        assert not missing, f"Response missing required fields: {missing}"

    def test_topic_is_string(self, monkeypatch):
        result = self._call_next_training(monkeypatch)
        _assert_str(result["topic"], "topic")

    def test_difficulty_is_numeric(self, monkeypatch):
        result = self._call_next_training(monkeypatch)
        _assert_float(result["difficulty"], "difficulty")

    def test_format_is_string(self, monkeypatch):
        result = self._call_next_training(monkeypatch)
        _assert_str(result["format"], "format")

    def test_expected_gain_is_numeric(self, monkeypatch):
        result = self._call_next_training(monkeypatch)
        _assert_float(result["expected_gain"], "expected_gain")

    def test_no_exercise_type_field(self, monkeypatch):
        """/next-training must NOT return 'exercise_type' (that belongs to /curriculum/next)."""
        result = self._call_next_training(monkeypatch)
        assert "exercise_type" not in result, (
            "exercise_type must not appear in /next-training response "
            "(belongs to /curriculum/next schema)"
        )

    def test_no_payload_field(self, monkeypatch):
        """/next-training must NOT return 'payload' (that belongs to /curriculum/next)."""
        result = self._call_next_training(monkeypatch)
        assert "payload" not in result, (
            "payload must not appear in /next-training response "
            "(belongs to /curriculum/next schema)"
        )


# ---------------------------------------------------------------------------
# 3. POST /game/finish (llm/seca/events/router.py)
# ---------------------------------------------------------------------------

_GAME_FINISH_REQUIRED = {
    "status",
    "new_rating",
    "confidence",
    "learning",
    "coach_action",
    "coach_content",
}
_COACH_ACTION_REQUIRED = {"type", "weakness", "reason"}
_COACH_CONTENT_REQUIRED = {"title", "description", "payload"}
_COACH_ACTION_TYPES = {"NONE", "REFLECT", "DRILL", "PUZZLE", "PLAN_UPDATE"}


def _make_game_finish_mocks(
    *,
    rating_before=1500.0,
    rating_after=1510.0,
    confidence_before=0.70,
    confidence_after=0.72,
    learning_delta=10.0,
):
    """Return (player, db) mocks suitable for calling finish_game() directly."""
    player = SimpleNamespace(
        id=1,
        rating=rating_before,
        confidence=confidence_before,
    )

    def _fake_refresh(obj):
        if obj is player:
            player.rating = rating_after
            player.confidence = confidence_after

    db = MagicMock()
    db.refresh.side_effect = _fake_refresh
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
        []
    )
    return player, db


def _call_finish_game(
    req_kwargs: dict,
    player,
    db,
):
    """Call finish_game() with all DB/storage dependencies mocked.

    Direct calls bypass the slowapi decorator's rate-limit counter
    backend by toggling `limiter.enabled = False` for the duration of
    the call — these tests exercise the handler logic, not the rate
    limit (the rate limit is verified by test_security_game_finish_rate_limit.py
    via AST inspection of the decorator)."""
    from llm.seca.events.router import finish_game, GameFinishRequest
    from llm.seca.shared_limiter import limiter
    from starlette.requests import Request

    fake_event = SimpleNamespace(id=99)
    req = GameFinishRequest(**req_kwargs)
    fake_request = Request({
        "type": "http", "method": "POST", "path": "/game/finish",
        "headers": [], "client": ("127.0.0.1", 0),
    })

    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        with (
            patch("llm.seca.events.router.EventStorage") as MockStorage,
            patch("llm.seca.events.router.SkillUpdater"),
        ):
            MockStorage.return_value.store_game.return_value = fake_event
            result = finish_game(req=req, player=player, request=fake_request, db=db)
    finally:
        limiter.enabled = prev_enabled

    return result


_DEFAULT_FINISH_REQ = {
    "pgn": (
        '[Event "Test"]\n'
        '[Site "?"]\n'
        '[Date "2025.01.01"]\n'
        '[Round "1"]\n'
        '[White "Player1"]\n'
        '[Black "Player2"]\n'
        '[Result "1-0"]\n'
        "\n"
        "1. e4 e5 2. Nf3 Nc6 1-0"
    ),
    "result": "win",
    "accuracy": 0.85,
    "weaknesses": {"tactics": 0.6},
}


class TestGameFinishContractSchema:
    """POST /game/finish response schema validation."""

    def test_response_has_all_required_top_level_fields(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        missing = _GAME_FINISH_REQUIRED - set(result.keys())
        assert not missing, f"Response missing required fields: {missing}"

    def test_status_is_stored(self):
        """status must always be the string 'stored' on success."""
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        assert result["status"] == "stored"

    def test_new_rating_is_numeric(self):
        player, db = _make_game_finish_mocks(rating_after=1510.0)
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_float(result["new_rating"], "new_rating")

    def test_confidence_is_numeric(self):
        player, db = _make_game_finish_mocks(confidence_after=0.72)
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_float(result["confidence"], "confidence")

    def test_learning_is_dict(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_dict(result["learning"], "learning")

    def test_learning_has_status_key(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        assert "status" in result["learning"], "learning dict missing 'status' key"

    def test_coach_action_has_all_required_fields(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_dict(result["coach_action"], "coach_action")
        missing = _COACH_ACTION_REQUIRED - set(result["coach_action"].keys())
        assert not missing, f"coach_action missing required fields: {missing}"

    def test_coach_content_has_all_required_fields(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_dict(result["coach_content"], "coach_content")
        missing = _COACH_CONTENT_REQUIRED - set(result["coach_content"].keys())
        assert not missing, f"coach_content missing required fields: {missing}"

    def test_coach_action_type_is_valid_enum(self):
        """coach_action.type must be one of the documented action types."""
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        action_type = result["coach_action"]["type"]
        assert (
            action_type in _COACH_ACTION_TYPES
        ), f"coach_action.type={action_type!r} not in {_COACH_ACTION_TYPES}"

    def test_coach_action_weakness_is_str_or_none(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_str_or_none(result["coach_action"]["weakness"], "coach_action.weakness")

    def test_coach_action_reason_is_str(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_str(result["coach_action"]["reason"], "coach_action.reason")

    def test_coach_content_title_is_str(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_str(result["coach_content"]["title"], "coach_content.title")

    def test_coach_content_description_is_str(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_str(result["coach_content"]["description"], "coach_content.description")

    def test_coach_content_payload_is_dict(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        _assert_dict(result["coach_content"]["payload"], "coach_content.payload")

    def test_new_rating_reflects_post_refresh_value(self):
        """new_rating must reflect the value AFTER db.refresh(), not the input."""
        player, db = _make_game_finish_mocks(rating_before=1500.0, rating_after=1512.0)
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        assert result["new_rating"] == 1512.0

    def test_confidence_reflects_post_refresh_value(self):
        player, db = _make_game_finish_mocks(confidence_before=0.70, confidence_after=0.74)
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        assert result["confidence"] == 0.74

    def test_safe_mode_sets_learning_status(self):
        """In SAFE_MODE (always True in prod), learning.status must be 'safe_mode'."""
        player, db = _make_game_finish_mocks()
        result = _call_finish_game(_DEFAULT_FINISH_REQ, player, db)
        # SAFE_MODE = True is hardcoded in llm/seca/runtime/safe_mode.py
        assert result["learning"]["status"] == "safe_mode"

    def test_result_draw_is_accepted(self):
        """'draw' is a valid result value."""
        player, db = _make_game_finish_mocks()
        result = _call_finish_game({**_DEFAULT_FINISH_REQ, "result": "draw"}, player, db)
        assert result["status"] == "stored"

    def test_result_loss_is_accepted(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game({**_DEFAULT_FINISH_REQ, "result": "loss"}, player, db)
        assert result["status"] == "stored"

    def test_empty_weaknesses_is_accepted(self):
        player, db = _make_game_finish_mocks()
        result = _call_finish_game({**_DEFAULT_FINISH_REQ, "weaknesses": {}}, player, db)
        assert result["status"] == "stored"


# ---------------------------------------------------------------------------
# 4. GET /auth/me — skill_vector field (P2-A contract)
# ---------------------------------------------------------------------------


class TestAuthMeContractSchema:
    """GET /auth/me response must include a 'skill_vector' dict field (P2-A)."""

    def _call_me(self, skill_vector_json: str = "{}"):
        from types import SimpleNamespace

        from llm.seca.auth.router import me

        player = SimpleNamespace(
            id="player-123",
            email="test@chess.com",
            rating=1450.0,
            confidence=0.65,
            skill_vector_json=skill_vector_json,
        )
        return me(player=player)

    def test_me_response_has_skill_vector_field(self):
        """skill_vector must be present in the /auth/me response."""
        result = self._call_me()
        assert "skill_vector" in result, (
            "GET /auth/me must include 'skill_vector' — "
            "Android client reads it to display weakness tags."
        )

    def test_skill_vector_is_dict(self):
        result = self._call_me()
        _assert_dict(result["skill_vector"], "skill_vector")

    def test_skill_vector_values_are_numeric(self):
        """All values in skill_vector must be numeric (float-compatible)."""
        result = self._call_me('{"tactics": 0.5, "endgame": 0.3}')
        for key, val in result["skill_vector"].items():
            _assert_float(val, f"skill_vector.{key}")

    def test_skill_vector_empty_when_no_history(self):
        """Empty JSON object yields an empty dict, not an error."""
        result = self._call_me("{}")
        assert result["skill_vector"] == {}

    def test_skill_vector_malformed_json_returns_empty(self):
        """Malformed skill_vector_json must not raise; returns empty dict."""
        result = self._call_me("not-valid-json")
        assert result["skill_vector"] == {}, (
            "Malformed skill_vector_json must degrade gracefully to empty dict."
        )

    def test_me_still_returns_core_fields(self):
        """P2-A addition must not drop existing fields: id, email, rating, confidence."""
        result = self._call_me()
        for field in ("id", "email", "rating", "confidence"):
            assert field in result, f"skill_vector addition must preserve field '{field}'"

    def test_non_numeric_skill_vector_values_are_filtered(self):
        """String values in skill_vector_json must be silently filtered out."""
        result = self._call_me('{"tactics": 0.6, "stale": "not-a-number"}')
        assert "stale" not in result["skill_vector"], (
            "Non-numeric entries must be excluded from skill_vector response."
        )
        assert "tactics" in result["skill_vector"]


# ---------------------------------------------------------------------------
# 6. Documented mismatches
# ---------------------------------------------------------------------------


class TestCoachEndpointMissing:
    """Contract mismatch: /coach endpoint does not exist."""

    def test_server_has_no_coach_route(self):
        """server.py must have no route registered at /coach."""
        import llm.server as server_module

        routes = [getattr(r, "path", None) for r in server_module.app.routes]
        assert "/coach" not in routes, (
            "/coach route unexpectedly found in server.py. "
            "Update docs/API_CONTRACTS.md to document the new endpoint."
        )

class TestNextTrainingSchemaConflict:
    """
    Contract mismatch: /next-training and /curriculum/next return different schemas.

    These two endpoints serve the same purpose but have incompatible response shapes.
    This test ensures neither endpoint silently starts returning the other's schema.
    """

    def test_next_training_does_not_return_exercise_type(self, monkeypatch):
        """Regression guard: /next-training must never start returning exercise_type."""
        import llm.server as server_module
        from llm.seca.curriculum.types import TrainingTask

        fake_task = TrainingTask(topic="endgame", difficulty=0.5, format="game", expected_gain=1.0)
        monkeypatch.setattr(
            server_module, "scheduler", SimpleNamespace(next_task=lambda *a: fake_task)
        )
        result = server_module.next_training(
            player_id="p1",
            player=SimpleNamespace(id="p1", rating=1200.0, confidence=0.5),
        )
        assert "exercise_type" not in result

    def test_curriculum_next_schema_has_exercise_type_not_format(self):
        """
        CurriculumGenerator.generate() returns a TrainingPlan with exercise_type,
        not format.  If someone renames this field, /curriculum/next contract breaks.
        """
        from llm.seca.curriculum.generator import CurriculumGenerator

        # Verify the attribute name on the return type
        sig = CurriculumGenerator.generate
        import inspect

        src = inspect.getsource(sig)
        assert "exercise_type" in src, (
            "CurriculumGenerator.generate() no longer uses 'exercise_type'. "
            "Update /curriculum/next contract in docs/API_CONTRACTS.md."
        )
        assert "format" not in src or "exercise_type" in src, (
            "CurriculumGenerator has changed its field naming — "
            "the /curriculum/next contract needs review."
        )

    def test_next_training_schema_fields_are_stable(self, monkeypatch):
        """
        The four fields of /next-training are: topic, difficulty, format, expected_gain.
        If the handler changes these names, the Android client breaks.
        """
        import llm.server as server_module
        from llm.seca.curriculum.types import TrainingTask

        fake_task = TrainingTask(
            topic="tactics", difficulty=0.7, format="puzzle", expected_gain=3.0
        )
        monkeypatch.setattr(
            server_module, "scheduler", SimpleNamespace(next_task=lambda *a: fake_task)
        )
        result = server_module.next_training(
            player_id="p2",
            player=SimpleNamespace(id="p2", rating=1200.0, confidence=0.5),
        )
        for field in ("topic", "difficulty", "format", "expected_gain"):
            assert field in result, (
                f"Field '{field}' removed from /next-training response. "
                "This breaks backward compatibility with Android clients."
            )


class TestCoachExecutorHandlerGap:
    """
    CoachExecutor handler coverage for PUZZLE and PLAN_UPDATE action types.

    Previously (before the fix) both action types had no dedicated handler and
    fell through to _handle_default, producing 'Keep playing' content regardless
    of the action type. The handlers have since been added. These tests verify the
    corrected behaviour.

    See docs/API_CONTRACTS.md — /game/finish — executor handler gap (now fixed).
    """

    def test_puzzle_action_returns_specific_content(self):
        """
        PUZZLE action now has a _handle_puzzle handler.
        The returned content must not be the generic 'Keep playing' fallback,
        and must reference the action's weakness theme.
        """
        from llm.seca.coach.executor import CoachExecutor

        action = SimpleNamespace(type="PUZZLE", weakness="tactics", reason="confidence drop")
        content = CoachExecutor().execute(action)
        assert content.title != "Keep playing", (
            "_handle_puzzle must return specific content, not the default fallback."
        )
        assert "tactics" in content.title.lower() or "puzzle" in content.title.lower(), (
            "PUZZLE content title should reference the weakness or 'puzzle'."
        )

    def test_plan_update_action_returns_specific_content(self):
        """
        PLAN_UPDATE action now has a _handle_plan_update handler.
        The returned content must not be the generic 'Keep playing' fallback,
        and must reference the action's weakness.
        """
        from llm.seca.coach.executor import CoachExecutor

        action = SimpleNamespace(type="PLAN_UPDATE", weakness="endgame", reason="repeated weakness")
        content = CoachExecutor().execute(action)
        assert content.title != "Keep playing", (
            "_handle_plan_update must return specific content, not the default fallback."
        )
        assert "endgame" in content.description.lower() or "endgame" in content.payload.get(
            "updated_focus", ""
        ), "PLAN_UPDATE content should reference the weakness."

    def test_game_finish_puzzle_response_is_consistent(self):
        """
        When PostGameCoachController decides PUZZLE, finish_game must return
        coach_content that is consistent with the action type — i.e. NOT 'Keep playing'.
        """
        player, db = _make_game_finish_mocks(
            rating_before=1500.0,
            rating_after=1502.0,
            confidence_before=0.80,
            confidence_after=0.70,  # confidence drop → triggers PUZZLE
        )
        result = _call_finish_game(
            {
                "pgn": (
                    '[Event "Test"]\n[Site "?"]\n[Date "2025.01.01"]\n'
                    '[Round "1"]\n[White "Player1"]\n[Black "Player2"]\n'
                    '[Result "*"]\n\n1. e4 e5 *'
                ),
                "result": "loss",
                "accuracy": 0.60,
                "weaknesses": {"tactics": 0.5},
            },
            player,
            db,
        )
        action_type = result["coach_action"]["type"]
        content_title = result["coach_content"]["title"]
        if action_type == "PUZZLE":
            assert content_title != "Keep playing", (
                f"coach_action.type='PUZZLE' but coach_content.title={content_title!r}. "
                "The executor handler gap was supposed to be fixed — "
                "_handle_puzzle must return puzzle-specific content."
            )

    def test_drill_and_reflect_handlers_are_consistent(self):
        """
        DRILL and REFLECT DO have handlers — these are the non-broken cases.
        They should produce content that matches the action type.
        """
        from llm.seca.coach.executor import CoachExecutor

        drill = SimpleNamespace(type="DRILL", weakness="tactics", reason="big drop")
        reflect = SimpleNamespace(type="REFLECT", weakness=None, reason="big gain")

        drill_content = CoachExecutor().execute(drill)
        reflect_content = CoachExecutor().execute(reflect)

        assert (
            drill_content.title != "Keep playing"
        ), "DRILL handler should produce specific content, not default"
        assert (
            reflect_content.title != "Keep playing"
        ), "REFLECT handler should produce specific content, not default"
        assert (
            "tactics" in drill_content.title.lower()
        ), "DRILL content should reference the weakness name"


# ---------------------------------------------------------------------------
# 7. GET / — root health endpoint (server.py)
# ---------------------------------------------------------------------------


class TestRootHealthEndpoint:
    """GET / root liveness probe."""

    def test_root_returns_status_ok(self):
        import llm.server as server_module

        result = server_module.root()
        assert result == {"status": "ok"}, f"Expected {{'status': 'ok'}}, got {result!r}"

    def test_root_and_health_return_identical_shape(self):
        import llm.server as server_module

        assert server_module.root() == server_module.health(), (
            "GET / and GET /health must return the same body"
        )


# ---------------------------------------------------------------------------
# 8. POST /analyze (server.py)
# ---------------------------------------------------------------------------

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

_FAKE_ENGINE_SIGNAL = {
    "evaluation": {"band": "equal", "side": "white"},
    "phase": "opening",
    "eval_delta": "stable",
    "last_move_quality": "unknown",
    "tactical_flags": [],
    "position_flags": [],
}


class TestAnalyzeContractSchema:
    """POST /analyze response schema validation.

    Pinned invariants
    -----------------
    ANALYZE_01  Response contains exactly one top-level field: 'engine_signal'.
    ANALYZE_02  'engine_signal' value is a dict.
    ANALYZE_03  Response reflects the value returned by build_engine_signal().
    ANALYZE_04  'startpos' FEN alias is accepted without error.
    ANALYZE_05  FEN with wrong number of parts raises pydantic ValidationError.
    ANALYZE_06  user_query longer than 2000 chars raises pydantic ValidationError.
    ANALYZE_07  verify_api_key dependency is present on the route (AST guard).
    ANALYZE_08  Route method is POST (AST guard).
    """

    def _call_analyze(self, monkeypatch, fen: str = _STARTING_FEN):
        import llm.server as server_module
        from starlette.requests import Request as StarletteRequest
        from starlette.datastructures import Headers

        monkeypatch.setattr(
            server_module, "build_engine_signal", lambda req: _FAKE_ENGINE_SIGNAL
        )
        # slowapi requires a real starlette Request; construct a minimal one.
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/analyze",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "query_string": b"",
        }
        fake_request = StarletteRequest(scope)
        req = server_module.AnalyzeRequest(fen=fen)
        return server_module.analyze(req=req, request=fake_request, _=None)

    def test_analyze_01_response_has_engine_signal_field(self, monkeypatch):
        result = self._call_analyze(monkeypatch)
        assert "engine_signal" in result, (
            "POST /analyze response must contain 'engine_signal'"
        )

    def test_analyze_02_engine_signal_is_dict(self, monkeypatch):
        result = self._call_analyze(monkeypatch)
        _assert_dict(result["engine_signal"], "engine_signal")

    def test_analyze_01_response_has_exactly_one_top_level_field(self, monkeypatch):
        result = self._call_analyze(monkeypatch)
        assert set(result.keys()) == {"engine_signal"}, (
            f"Response must have exactly {{'engine_signal'}} at top level, "
            f"got {set(result.keys())}"
        )

    def test_analyze_03_engine_signal_reflects_build_engine_signal_return(self, monkeypatch):
        result = self._call_analyze(monkeypatch)
        assert result["engine_signal"] == _FAKE_ENGINE_SIGNAL, (
            "engine_signal must be the value returned by build_engine_signal()"
        )

    def test_analyze_04_startpos_alias_accepted(self, monkeypatch):
        """'startpos' is a valid FEN alias; must not raise."""
        result = self._call_analyze(monkeypatch, fen="startpos")
        assert "engine_signal" in result

    def test_analyze_05_invalid_fen_raises_validation_error(self):
        """FEN with wrong number of space-separated parts must be rejected."""
        import pytest
        from pydantic import ValidationError

        import llm.server as server_module

        with pytest.raises(ValidationError):
            server_module.AnalyzeRequest(fen="not-a-valid-fen")

    def test_analyze_06_user_query_too_long_raises_validation_error(self):
        """user_query longer than 2000 characters must be rejected."""
        import pytest
        from pydantic import ValidationError

        import llm.server as server_module

        with pytest.raises(ValidationError):
            server_module.AnalyzeRequest(fen=_STARTING_FEN, user_query="x" * 2001)

    def test_analyze_07_route_has_verify_api_key_dependency(self):
        """verify_api_key must be a dependency on the /analyze route (AST guard)."""
        import ast
        from pathlib import Path

        src = (Path(__file__).parent.parent / "server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "analyze":
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            args_src = ast.unparse(node.args)
            # verify_api_key appears either as a decorator argument or as a Depends call
            combined = " ".join(decorators) + " " + args_src
            assert "verify_api_key" in combined, (
                "verify_api_key dependency missing from /analyze route — "
                "endpoint would be unauthenticated"
            )
            return

        raise AssertionError("analyze() function not found in server.py")

    def test_analyze_08_route_method_is_post(self):
        """The /analyze route must be registered as POST, not GET."""
        import ast
        from pathlib import Path

        src = (Path(__file__).parent.parent / "server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "analyze":
                continue
            for dec in node.decorator_list:
                dec_str = ast.unparse(dec)
                if "/analyze" in dec_str:
                    assert "app.post" in dec_str, (
                        f"/analyze must be registered as POST, got: {dec_str!r}"
                    )
                    return

        raise AssertionError("@app.<method>('/analyze') decorator not found")


# ---------------------------------------------------------------------------
# /chat + /chat/stream + /live/move boundary validators
# ---------------------------------------------------------------------------


def _valid_engine_signal() -> dict:
    """A minimal engine_signal dict that satisfies EngineSignalSchema."""
    return {
        "evaluation": {"type": "cp", "band": "small_advantage", "side": "white"},
        "eval_delta": "stable",
        "last_move_quality": "good",
        "tactical_flags": [],
        "position_flags": [],
        "phase": "middlegame",
    }


class TestChatResponseValidation:
    """Boundary validator for POST /chat and POST /chat/stream responses.

    Mirrors the /explain validator: structural Pydantic check + Mode-2
    negative content rules on the reply field, with mode pinned to CHAT_V1.
    """

    def _payload(self, **overrides) -> dict:
        base = {
            "reply": "The position is balanced; both sides have active pieces.",
            "engine_signal": _valid_engine_signal(),
            "mode": "CHAT_V1",
        }
        base.update(overrides)
        return base

    def test_valid_payload_passes(self):
        from llm.rag.validators.explain_response_schema import validate_chat_response

        validated = validate_chat_response(self._payload())
        assert validated.mode == "CHAT_V1"
        assert validated.reply.strip() != ""

    def test_missing_reply_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        payload = self._payload()
        del payload["reply"]
        with pytest.raises(ExplainSchemaError):
            validate_chat_response(payload)

    def test_empty_reply_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="non-empty"):
            validate_chat_response(self._payload(reply="   "))

    def test_forbidden_move_pattern_raises(self):
        """Invented chess moves (Nf3, Qh5, ...) must be caught at the boundary."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="Mode-2"):
            validate_chat_response(self._payload(reply="The engine prefers Nf3 here."))

    def test_forbidden_mate_claim_raises(self):
        """Mate claims must be caught at the boundary."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="Mode-2"):
            validate_chat_response(self._payload(reply="It is checkmate in two."))

    def test_speculative_language_raises(self):
        """Mode-2 forbids speculative language ("should", "consider", etc.)."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="Mode-2"):
            validate_chat_response(
                self._payload(reply="White should consider activating the rook.")
            )

    def test_wrong_mode_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="schema"):
            validate_chat_response(self._payload(mode="EXPLAIN_V1"))

    def test_bad_engine_signal_band_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        bad_signal = _valid_engine_signal()
        bad_signal["evaluation"]["band"] = "completely_winning"  # not in enum
        with pytest.raises(ExplainSchemaError, match="schema"):
            validate_chat_response(self._payload(engine_signal=bad_signal))

    def test_engine_signal_missing_phase_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        bad_signal = _valid_engine_signal()
        del bad_signal["phase"]
        with pytest.raises(ExplainSchemaError, match="schema"):
            validate_chat_response(self._payload(engine_signal=bad_signal))

    def test_extra_fields_are_ignored(self):
        """Lenient by default — future fields don't break old clients."""
        from llm.rag.validators.explain_response_schema import validate_chat_response

        payload = self._payload()
        payload["extra_diagnostic"] = {"latency_ms": 42}
        validated = validate_chat_response(payload)
        assert validated.mode == "CHAT_V1"

    # -----------------------------------------------------------------------
    # Sprint 5.A: structure + semantic enforcement at the boundary.
    #
    # Pre-Sprint-5.A, only ``validate_mode_2_negative`` ran inside
    # ``validate_chat_response``.  Sprint 5.A added the structure and
    # semantic gates so the matrix in ``docs/TESTING.md`` (rows 3, 8, 9)
    # is honest about what's actually enforced at the live API edge.
    # These tests pick replies that violate ONLY the new gates (the
    # negative validator passes them) so the structure / semantic
    # contribution is unambiguous.
    # -----------------------------------------------------------------------

    def test_chat_forbidden_section_plan_raises(self):
        """``plan`` is on validate_mode_2_structure's FORBIDDEN_SECTIONS
        but absent from validate_mode_2_negative.FORBIDDEN_PATTERNS — this
        reply passes the negative gate and must be caught by structure."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="structure"):
            validate_chat_response(
                self._payload(reply="Develop your pieces and form a concrete plan.")
            )

    def test_chat_forbidden_section_recommended_move_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="structure"):
            validate_chat_response(
                self._payload(reply="The recommended move keeps activity.")
            )

    def test_chat_speculative_engine_token_raises(self):
        """``engine`` is on validate_mode_2_semantic's
        FORBIDDEN_ENGINE_SPECULATION list (rejected unconditionally,
        regardless of band) but the bare token is not in the negative
        validator's FORBIDDEN_PATTERNS (only ``the engine wants`` is)."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_chat_response(
                self._payload(
                    reply="The position is solid; the engine sees a small edge."
                )
            )

    def test_chat_equal_band_describes_advantage_raises(self):
        """When engine_signal says band='equal', the reply must NOT use
        FORBIDDEN_EQUAL tokens (slight advantage / better / winning /
        initiative / pressure).  ``initiative`` is the canary picked
        here because it does not collide with any negative regex."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        equal_signal = _valid_engine_signal()
        equal_signal["evaluation"]["band"] = "equal"
        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_chat_response(
                self._payload(
                    reply="White holds a clear initiative across the board.",
                    engine_signal=equal_signal,
                )
            )

    def test_chat_mate_missing_inevitability_raises(self):
        """When engine_signal says type='mate', the reply MUST contain
        ``inevitable`` or ``forced`` — otherwise the mate framing reads
        as ambiguous and semantic rejects it."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        mate_signal = _valid_engine_signal()
        mate_signal["evaluation"]["type"] = "mate"
        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_chat_response(
                self._payload(
                    reply="The result here will be decided in a few moves.",
                    engine_signal=mate_signal,
                )
            )

    def test_chat_invented_tactic_without_flag_raises(self):
        """When tactical_flags == [] the reply must NOT invent tactical
        terms (fork / pin / sacrifice / attack / threat).  ``fork`` is
        the canary because it's absent from the negative regex set."""
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_chat_response,
        )

        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_chat_response(
                self._payload(
                    reply="A knight fork on the next move would shift the balance."
                )
            )


class TestLiveMoveResponseValidation:
    """Boundary validator for POST /live/move response.

    Empty hint is allowed (deterministic-fallback path); non-empty hints
    pass through Mode-2 negative validation.  move_quality may be any of
    the EngineSignalSchema.last_move_quality buckets, including "unknown".
    """

    def _payload(self, **overrides) -> dict:
        base = {
            "status": "ok",
            "hint": "Solid central pawn. Develop a knight to claim more space.",
            "engine_signal": _valid_engine_signal(),
            "move_quality": "good",
            "mode": "LIVE_V1",
        }
        base.update(overrides)
        return base

    def test_valid_payload_passes(self):
        from llm.rag.validators.explain_response_schema import validate_live_move_response

        validated = validate_live_move_response(self._payload())
        assert validated.mode == "LIVE_V1"
        assert validated.status == "ok"

    def test_empty_hint_is_allowed(self):
        """API_CONTRACTS.md §4 explicitly allows empty hint and forbids
        the client from substituting null."""
        from llm.rag.validators.explain_response_schema import validate_live_move_response

        validated = validate_live_move_response(self._payload(hint=""))
        assert validated.hint == ""

    def test_whitespace_hint_is_allowed(self):
        """Whitespace-only hint counts as empty for content-validation purposes."""
        from llm.rag.validators.explain_response_schema import validate_live_move_response

        validated = validate_live_move_response(self._payload(hint="   "))
        assert validated.hint == "   "

    def test_forbidden_move_in_non_empty_hint_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        with pytest.raises(ExplainSchemaError, match="Mode-2"):
            validate_live_move_response(
                self._payload(hint="Castle kingside with 0-0 next move.")
            )

    def test_unknown_move_quality_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        with pytest.raises(ExplainSchemaError, match="schema"):
            validate_live_move_response(self._payload(move_quality="excellent_blunder"))

    def test_unknown_string_for_move_quality_passes(self):
        """live_move_pipeline returns "unknown" when the engine signal lacks
        a quality bucket — that value must be accepted."""
        from llm.rag.validators.explain_response_schema import validate_live_move_response

        validated = validate_live_move_response(self._payload(move_quality="unknown"))
        assert validated.move_quality == "unknown"

    def test_wrong_status_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        with pytest.raises(ExplainSchemaError, match="schema"):
            validate_live_move_response(self._payload(status="error"))

    def test_wrong_mode_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        with pytest.raises(ExplainSchemaError, match="schema"):
            validate_live_move_response(self._payload(mode="CHAT_V1"))

    def test_dynamic_adaptation_extra_field_is_ignored(self):
        """server.py adds dynamic_adaptation to the response payload but the
        field is not in API_CONTRACTS.md §4 — verify the validator tolerates
        the extra field (lenient by default)."""
        from llm.rag.validators.explain_response_schema import validate_live_move_response

        payload = self._payload()
        payload["dynamic_adaptation"] = True
        validated = validate_live_move_response(payload)
        assert validated.mode == "LIVE_V1"

    # -----------------------------------------------------------------------
    # Sprint 5.A: structure + semantic enforcement at the live-move boundary.
    # Mirrors the chat-boundary tests above.  Empty hints still skip all
    # content gates (the deterministic-fallback path can emit "" — see
    # ``test_empty_hint_is_allowed`` above).
    # -----------------------------------------------------------------------

    def test_live_forbidden_section_plan_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        with pytest.raises(ExplainSchemaError, match="structure"):
            validate_live_move_response(
                self._payload(hint="Form a concrete plan and trade pieces.")
            )

    def test_live_speculative_engine_token_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_live_move_response(
                self._payload(hint="Solid move; the engine evaluates this as small edge.")
            )

    def test_live_equal_band_describes_advantage_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        equal_signal = _valid_engine_signal()
        equal_signal["evaluation"]["band"] = "equal"
        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_live_move_response(
                self._payload(
                    hint="Black has the initiative now.",
                    engine_signal=equal_signal,
                )
            )

    def test_live_invented_tactic_without_flag_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_live_move_response(
                self._payload(hint="Watch out — a knight fork could land next move.")
            )

    def test_live_mate_missing_inevitability_raises(self):
        from llm.rag.validators.explain_response_schema import (
            ExplainSchemaError,
            validate_live_move_response,
        )

        mate_signal = _valid_engine_signal()
        mate_signal["evaluation"]["type"] = "mate"
        with pytest.raises(ExplainSchemaError, match="semantic"):
            validate_live_move_response(
                self._payload(
                    hint="The position will resolve in a few moves.",
                    engine_signal=mate_signal,
                )
            )


class TestDeterministicFallbacksPassBoundaryValidator:
    """Regression: the deterministic chat / live-move fallback prose must
    pass the new Mode-2 boundary validators on every shape of input.

    Pre-deploy probe for #2 caught two real bugs that this test pins:
      - _COACHING_ADVICE entries containing forbidden words ("Calculate",
        "Consider") would have 500'd every chat call when Ollama was down.
      - _build_reply_deterministic echoed the user's raw query into the
        reply via f-string, propagating any "should" / "consider" / "Nf3"
        the user typed into the response, which the validator then rejects.

    Both paths run unconditionally when the LLM is unavailable, so this
    has to hold on every (fen, messages, voice) combination — not just
    the happy path.
    """

    _FENS = [
        "startpos",
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        "r1bqkbnr/pp2pppp/2n5/2pp4/3P4/2N2N2/PPP1PPPP/R1BQKB1R w KQkq - 0 4",
        "8/8/8/4k3/8/4K3/4P3/8 w - - 0 1",
        "6k1/5ppp/8/8/8/8/r7/2K5 w - - 0 1",
        "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2",
    ]

    _ADVERSARIAL_QUERIES = [
        # Plain queries
        "What's happening here?",
        "How should I think about this position?",
        "Is this position good or bad for me?",
        "Explain the imbalances.",
        # Adversarial: the user's text contains MULTIPLE forbidden Mode-2
        # tokens.  If the deterministic fallback echoes any of these into
        # the reply, the boundary validator rejects → 500.  Pinning this
        # explicitly so a future "let's just put the user's question in
        # the reply for context" refactor can't quietly re-break it.
        "Should I consider Nf3 here?",
        "Calculate the variation Qh5 — what's the line?",
        "Is checkmate forced after Nf3?",
    ]

    _VOICES = [None, "formal", "conversational", "terse"]

    def test_chat_deterministic_fallback_passes_validator(self):
        from unittest.mock import patch
        import llm.seca.coach.chat_pipeline as chat_mod
        from llm.seca.coach.chat_pipeline import generate_chat_reply, ChatTurn
        from llm.rag.validators.explain_response_schema import (
            validate_chat_response,
        )

        with patch.object(chat_mod, "_LLM_AVAILABLE", False):
            for fen in self._FENS:
                for query in self._ADVERSARIAL_QUERIES:
                    messages = [ChatTurn(role="user", content=query)]
                    for voice in self._VOICES:
                        result = generate_chat_reply(
                            fen=fen, messages=messages, coach_voice=voice
                        )
                        validate_chat_response(
                            {
                                "reply": result.reply,
                                "engine_signal": result.engine_signal,
                                "mode": result.mode,
                            }
                        )

    def test_chat_deterministic_fallback_passes_with_prior_history(self):
        """Multi-turn case: prior user-turn content is also no longer
        echoed verbatim, so adversarial tokens in earlier messages don't
        leak into the current reply either."""
        from unittest.mock import patch
        import llm.seca.coach.chat_pipeline as chat_mod
        from llm.seca.coach.chat_pipeline import generate_chat_reply, ChatTurn
        from llm.rag.validators.explain_response_schema import (
            validate_chat_response,
        )

        history = [
            ChatTurn(role="user", content="Should I calculate every variation?"),
            ChatTurn(role="assistant", content="Some advice"),
            ChatTurn(role="user", content="What about pawn breaks?"),
        ]
        with patch.object(chat_mod, "_LLM_AVAILABLE", False):
            for voice in self._VOICES:
                result = generate_chat_reply(
                    fen="startpos", messages=history, coach_voice=voice
                )
                validate_chat_response(
                    {
                        "reply": result.reply,
                        "engine_signal": result.engine_signal,
                        "mode": result.mode,
                    }
                )

    def test_live_deterministic_fallback_passes_validator(self):
        from unittest.mock import patch
        import llm.seca.coach.live_move_pipeline as live_mod
        from llm.seca.coach.live_move_pipeline import generate_live_reply
        from llm.rag.validators.explain_response_schema import (
            validate_live_move_response,
        )

        moves = ["e2e4", "g1f3", "b1c3", "f7f6", "d2d4"]
        with patch.object(live_mod, "_LLM_AVAILABLE", False):
            for fen in self._FENS:
                for uci in moves:
                    for style in (None, "simple", "intermediate", "advanced"):
                        result = generate_live_reply(
                            fen=fen, uci=uci, explanation_style=style
                        )
                        validate_live_move_response(
                            {
                                "status": "ok",
                                "hint": result.hint,
                                "engine_signal": result.engine_signal,
                                "move_quality": result.move_quality,
                                "mode": result.mode,
                            }
                        )

    def test_coaching_advice_table_is_mode2_clean(self):
        """Every entry in _COACHING_ADVICE must independently pass
        validate_mode_2_negative — pins the table itself rather than
        relying on the integration test above to find a regression
        through (fen × query × voice) coverage."""
        from llm.seca.coach.chat_pipeline import _COACHING_ADVICE
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        for question_type, by_skill in _COACHING_ADVICE.items():
            for skill_level, advice in by_skill.items():
                validate_mode_2_negative(advice)


class TestChatStreamBoundaryValidation:
    """The /chat/stream endpoint validates BEFORE any bytes are streamed.

    Confirms structurally — reading the source — that validate_chat_response
    is invoked before StreamingResponse is constructed, so a contract failure
    propagates as a clean HTTP 500 instead of a half-delivered SSE stream.
    """

    def test_chat_stream_validates_before_streaming(self):
        import ast
        from pathlib import Path

        src = (Path(__file__).parent.parent / "server.py").read_text()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name != "chat_stream":
                continue

            body_str = "\n".join(ast.unparse(stmt) for stmt in node.body)
            validate_pos = body_str.find("validate_chat_response")
            stream_pos = body_str.find("StreamingResponse")

            assert validate_pos != -1, "validate_chat_response not called in /chat/stream"
            assert stream_pos != -1, "StreamingResponse not used in /chat/stream"
            assert validate_pos < stream_pos, (
                "validate_chat_response must precede StreamingResponse in /chat/stream"
            )
            return

        raise AssertionError("async def chat_stream not found in server.py")
