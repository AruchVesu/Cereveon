"""
Backend contract tests for POST /engine/eval — Android client expectations.

These tests are deterministic: no live engine, no live database, no LLM calls.
They pin the invariants that the Android EngineEvalApiClient depends on.

Context
-------
Android calls POST /engine/eval (host_app.py) after each human move to obtain:
  - score: centipawn evaluation from White's perspective (int | null)
  - best_move: engine's preferred response in UCI notation (str | null)
  - source: one of "engine", "cache", "book"

The endpoint is unauthenticated — no X-Api-Key header is required.
This is intentional and must not change without updating the Android client.

Pinned invariants
-----------------
 1. NO_AUTH_ON_EVAL_POST  — POST /engine/eval has no Depends(verify_api_key).
 2. NO_AUTH_ON_EVAL_GET   — GET  /engine/eval has no Depends(verify_api_key).
 3. RESPONSE_HAS_SCORE    — response includes "score" key.
 4. RESPONSE_HAS_BEST_MOVE — response includes "best_move" key.
 5. RESPONSE_HAS_SOURCE   — response includes "source" key.
 6. SCORE_IS_INT_OR_NULL  — score is int or None (never a float).
 7. SCORE_POSITIVE_MEANS_WHITE_ADVANTAGE — positive score → White ahead.
 8. SCORE_NEGATIVE_MEANS_BLACK_ADVANTAGE — negative score → Black ahead.
 9. BEST_MOVE_IS_STR_OR_NULL — best_move is a string or None.
10. BEST_MOVE_UCI_FORMAT   — non-null best_move matches UCI pattern.
11. SOURCE_IS_VALID_ENUM   — source is one of "engine", "cache", "book".
12. METRICS_KEY_PRESENT    — _metrics dict is always in the response.
13. EVAL_POST_IS_ASYNC     — eval_position is an async function (await-able).
14. CENTIPAWN_FORMAT_ROUNDTRIP — 100 cp = 1 pawn (scale contract for Android).
15. NULL_SCORE_PATH        — score=None response is valid (engine unavailable).
16. AST_EVAL_POST_ROUTE_METHOD — eval_position is decorated with @app.post.
17. AST_EVAL_GET_ROUTE_METHOD  — eval_position_query is decorated with @app.get.
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_HOST_APP_PY = Path(__file__).resolve().parent.parent / "host_app.py"
_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_VALID_SOURCES = {"engine", "cache", "book"}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _parse_host_app() -> ast.Module:
    return ast.parse(_HOST_APP_PY.read_text(encoding="utf-8"))


def _get_function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _depends_on(func_def, target: str) -> bool:
    """Return True if func_def has a Depends(target) in its argument defaults."""
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


def _has_decorator_method(func_def, method: str) -> bool:
    """Return True if func_def has @app.<method>(...) decorator."""
    for decorator in func_def.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        fn = decorator.func
        if isinstance(fn, ast.Attribute) and fn.attr == method:
            return True
    return False


def _is_async_function(func_def) -> bool:
    return isinstance(func_def, ast.AsyncFunctionDef)


# ---------------------------------------------------------------------------
# 1–2  No-auth invariants (AST)
# ---------------------------------------------------------------------------


class TestEngineEvalNoAuth:
    """POST and GET /engine/eval must require no API key — Android calls them unauthenticated."""

    def test_eval_position_has_no_verify_api_key(self):
        """NO_AUTH_ON_EVAL_POST: POST /engine/eval must not have Depends(verify_api_key)."""
        tree = _parse_host_app()
        func = _get_function(tree, "eval_position")
        assert func is not None, "eval_position() not found in host_app.py"
        assert not _depends_on(func, "verify_api_key"), (
            "POST /engine/eval must NOT have Depends(verify_api_key). "
            "Android calls this endpoint without an API key. "
            "Adding auth here would break all Android engine evaluations."
        )

    def test_eval_position_query_has_no_verify_api_key(self):
        """NO_AUTH_ON_EVAL_GET: GET /engine/eval must not have Depends(verify_api_key)."""
        tree = _parse_host_app()
        func = _get_function(tree, "eval_position_query")
        assert func is not None, "eval_position_query() not found in host_app.py"
        assert not _depends_on(func, "verify_api_key"), (
            "GET /engine/eval must NOT have Depends(verify_api_key). "
            "This endpoint is intentionally open."
        )


# ---------------------------------------------------------------------------
# 3–12  Response schema (mock-based)
# ---------------------------------------------------------------------------


def _make_engine_service_mock(
    *,
    score: int | None = 42,
    best_move: str | None = "e2e4",
    source: str = "engine",
    cache_hit: bool = False,
):
    async def _evaluate_with_metrics(*, fen, moves, movetime, nodes):
        result = {"score": score, "best_move": best_move, "source": source}
        metrics = {
            "cache_hit": cache_hit,
            "source": source,
            "engine_wait_ms": 1.0,
            "engine_eval_ms": 5.0,
            "total_ms": 6.0,
        }
        return result, metrics

    return _evaluate_with_metrics


def _call_eval_position(monkeypatch, **kwargs):
    from llm import host_app

    class _FakeEvaluator:
        default_nodes = 5000

        def resolve_limits(self, *, movetime, nodes):
            if movetime is None and nodes is None:
                return None, self.default_nodes
            return movetime, nodes

    # Disable the rate limiter so direct function calls don't need a real Request.
    monkeypatch.setattr(host_app._limiter, "enabled", False)
    monkeypatch.setattr(host_app, "engine_eval", _FakeEvaluator())
    monkeypatch.setattr(
        host_app.engine_service,
        "evaluate_with_metrics",
        _make_engine_service_mock(**kwargs),
    )

    async def _run():
        return await host_app.eval_position(
            MagicMock(), host_app.EngineEvalRequest(fen="startpos")
        )

    return asyncio.run(_run())


class TestEngineEvalResponseSchema:
    """Response schema invariants — the Android client parses exactly these fields."""

    def test_response_has_score_key(self, monkeypatch):
        """RESPONSE_HAS_SCORE"""
        result = _call_eval_position(monkeypatch)
        assert "score" in result, "Response missing required field 'score'"

    def test_response_has_best_move_key(self, monkeypatch):
        """RESPONSE_HAS_BEST_MOVE"""
        result = _call_eval_position(monkeypatch)
        assert "best_move" in result, "Response missing required field 'best_move'"

    def test_response_has_source_key(self, monkeypatch):
        """RESPONSE_HAS_SOURCE"""
        result = _call_eval_position(monkeypatch)
        assert "source" in result, "Response missing required field 'source'"

    def test_score_is_int_or_null(self, monkeypatch):
        """SCORE_IS_INT_OR_NULL — must not be float; Android parses as Int?"""
        result = _call_eval_position(monkeypatch, score=42)
        assert isinstance(result["score"], (int, type(None))), (
            f"score must be int|None (centipawns), got {type(result['score']).__name__}"
        )

    def test_positive_score_means_white_advantage(self, monkeypatch):
        """SCORE_POSITIVE_MEANS_WHITE_ADVANTAGE"""
        result = _call_eval_position(monkeypatch, score=100)
        assert result["score"] == 100
        assert result["score"] > 0

    def test_negative_score_means_black_advantage(self, monkeypatch):
        """SCORE_NEGATIVE_MEANS_BLACK_ADVANTAGE"""
        result = _call_eval_position(monkeypatch, score=-180)
        assert result["score"] == -180
        assert result["score"] < 0

    def test_best_move_is_str_or_null(self, monkeypatch):
        """BEST_MOVE_IS_STR_OR_NULL"""
        result = _call_eval_position(monkeypatch, best_move="e2e4")
        assert isinstance(result["best_move"], (str, type(None))), (
            f"best_move must be str|None, got {type(result['best_move']).__name__}"
        )

    def test_best_move_is_valid_uci_when_non_null(self, monkeypatch):
        """BEST_MOVE_UCI_FORMAT — Android renders this in the UI."""
        result = _call_eval_position(monkeypatch, best_move="e2e4")
        bm = result["best_move"]
        if bm is not None:
            assert _UCI_RE.match(bm), (
                f"best_move={bm!r} is not a valid UCI string. "
                "Android uses this to highlight the engine's preferred move."
            )

    def test_source_is_valid_enum_value(self, monkeypatch):
        """SOURCE_IS_VALID_ENUM — Android parses "engine"|"cache"|"book"."""
        for source in _VALID_SOURCES:
            result = _call_eval_position(monkeypatch, source=source)
            assert result["source"] in _VALID_SOURCES, (
                f"source={result['source']!r} not in {_VALID_SOURCES}"
            )

    def test_metrics_key_is_always_present(self, monkeypatch):
        """METRICS_KEY_PRESENT — _metrics must appear (diagnostic field)."""
        result = _call_eval_position(monkeypatch)
        assert "_metrics" in result, "Response missing '_metrics' diagnostic field"
        assert isinstance(result["_metrics"], dict)

    def test_null_score_is_valid_response(self, monkeypatch):
        """NULL_SCORE_PATH — Android shows '?' for null score; this path must work."""
        result = _call_eval_position(monkeypatch, score=None)
        assert result["score"] is None, (
            "score=None (engine unavailable) must be preserved in the response"
        )


# ---------------------------------------------------------------------------
# 13  Async function check (AST)
# ---------------------------------------------------------------------------


class TestEngineEvalAsync:
    """EVAL_POST_IS_ASYNC — eval_position must be async (coroutine function)."""

    def test_eval_position_is_async(self):
        tree = _parse_host_app()
        func = _get_function(tree, "eval_position")
        assert func is not None, "eval_position() not found in host_app.py"
        assert _is_async_function(func), (
            "eval_position() must be an async def. "
            "Removing async would break the engine pool await chain."
        )


# ---------------------------------------------------------------------------
# 14  Centipawn scale contract
# ---------------------------------------------------------------------------


class TestCentipawnScaleContract:
    """CENTIPAWN_FORMAT_ROUNDTRIP — 100 cp = 1 pawn (Android display contract)."""

    def test_one_hundred_centipawns_equals_one_pawn(self, monkeypatch):
        """100 cp from the engine should display as +1.00 in the Android dock."""
        result = _call_eval_position(monkeypatch, score=100)
        score = result["score"]
        assert score == 100, f"Expected score=100, got {score}"
        # Verify the Android conversion: score / 100.0 == 1.00
        assert abs(score / 100.0 - 1.0) < 1e-9, (
            "100 cp must equal exactly 1.0 pawn units in the Android display layer"
        )

    def test_negative_centipawns_scale_correctly(self, monkeypatch):
        result = _call_eval_position(monkeypatch, score=-80)
        score = result["score"]
        assert score == -80
        assert abs(score / 100.0 - (-0.80)) < 1e-9


# ---------------------------------------------------------------------------
# 15–17  Route method AST guards
# ---------------------------------------------------------------------------


class TestEngineEvalRouteMethod:
    """AST inspection: eval_position and eval_position_query route decorators."""

    def test_eval_position_is_post_route(self):
        """AST_EVAL_POST_ROUTE_METHOD: eval_position must be @app.post(...)."""
        tree = _parse_host_app()
        func = _get_function(tree, "eval_position")
        assert func is not None
        assert _has_decorator_method(func, "post"), (
            "eval_position() must be decorated with @app.post(). "
            "Android sends POST requests; changing this breaks the Android client."
        )

    def test_eval_position_query_is_get_route(self):
        """AST_EVAL_GET_ROUTE_METHOD: eval_position_query must be @app.get(...)."""
        tree = _parse_host_app()
        func = _get_function(tree, "eval_position_query")
        assert func is not None
        assert _has_decorator_method(func, "get"), (
            "eval_position_query() must be decorated with @app.get()."
        )
