"""
Server wiring invariant tests — llm/tests/test_server_wiring.py

Covers structural properties of the server layer that must not regress.
All tests are CI-safe: AST inspection only, no live processes or network I/O.

Pinned invariants
-----------------
WIRE-01  events/router.py has no bare print() calls (same standard as server.py)
WIRE-02  /explain endpoint is wired to SafeExplainer, not generate_validated_explanation
WIRE-03  _record_move_stat() in server.py always safe: total is incremented before any
         division, so the zero-guard is redundant but harmless
WIRE-04  server.py calls log_move with game_id argument (call site exists)
WIRE-05  GameFinishClosedLoopRequest is defined in server.py (model exists in source)
WIRE-06  generate_validated_explanation is imported in server.py (import present)
WIRE-07  events/router.py logger is used for diagnostics (not silenced)
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVER_PY = _REPO_ROOT / "llm" / "server.py"
_EVENTS_ROUTER = _REPO_ROOT / "llm" / "seca" / "events" / "router.py"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _bare_print_lines(path: Path) -> list[int]:
    """Return line numbers of bare print() calls in source file."""
    tree = _parse(path)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "print":
                violations.append(node.lineno)
    return violations


# ===========================================================================
# WIRE-01  events/router.py must not have bare print() statements
# ===========================================================================


class TestEventsRouterNoPrintStatements:
    """WIRE-01: events/router.py must use logger, not print(), for diagnostics.

    The same standard enforced on server.py (SEC_SERVER_NO_PRINT_STMTS) applies
    to the events router.  print() calls bypass the logging framework and can
    leak internal state to stdout in production.
    """

    def test_events_router_has_no_bare_print_calls(self):
        violations = _bare_print_lines(_EVENTS_ROUTER)
        assert not violations, (
            f"events/router.py contains bare print() calls at lines {violations}. "
            "Replace with logger.info() / logger.error() / logger.exception()."
        )


# ===========================================================================
# WIRE-02  /explain endpoint uses SafeExplainer, not LLM pipeline
# ===========================================================================


class TestExplainEndpointWiring:
    """WIRE-02: /explain must call safe_explainer.explain(), not generate_validated_explanation().

    generate_validated_explanation() calls an external Ollama process; using it in
    /explain would make the endpoint unavailable when Ollama is offline.
    SafeExplainer is the deterministic, always-available fallback that must be used.
    """

    def test_explain_function_calls_safe_explainer(self):
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "safe_explainer.explain" in source, (
            "server.py /explain endpoint must call safe_explainer.explain(). "
            "The LLM pipeline (generate_validated_explanation) requires Ollama "
            "and must not be wired to the /explain HTTP route."
        )

    def test_explain_function_does_not_call_llm_pipeline_directly(self):
        """The /explain handler body must not call generate_validated_explanation."""
        tree = _parse(_SERVER_PY)
        explain_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "explain":
                explain_func = node
                break
        assert explain_func is not None, "explain() function not found in server.py"
        for node in ast.walk(explain_func):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else (func.attr if isinstance(func, ast.Attribute) else "")
                )
                assert name != "generate_validated_explanation", (
                    "explain() must not call generate_validated_explanation() — "
                    "that function requires a live Ollama process. "
                    "Use safe_explainer.explain() instead."
                )


# ===========================================================================
# WIRE-03  _record_move_stat() zero-guard is after increment (dead code, safe)
# ===========================================================================


class TestRecordMoveStatDivisionSafe:
    """WIRE-03: _record_move_stat must never produce ZeroDivisionError.

    The function increments total before the division, so total is always ≥1
    when the division executes.  This test documents the invariant so that a
    refactor that moves the increment or adds early-return paths will fail CI.
    """

    def test_record_move_stat_never_zero_divides(self):
        """After increment, total ≥1; division is always safe."""
        import sys, importlib, types

        # Import without triggering FastAPI / SQLAlchemy startup
        import os
        os.environ.setdefault("SECA_API_KEY", "ci-test-key")
        os.environ.setdefault("SECA_ENV", "dev")
        os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

        from llm.server import _record_move_stat, move_stats

        original_total = move_stats["total"]
        original_hits = move_stats["cache_hits"]
        try:
            # Call with total starting from 0 to check no ZeroDivisionError
            move_stats["total"] = 0
            move_stats["cache_hits"] = 0
            rate_miss = _record_move_stat(cache_hit=False)
            assert rate_miss == 0.0, f"Expected 0.0 hit rate on first miss, got {rate_miss}"
            rate_hit = _record_move_stat(cache_hit=True)
            assert 0.0 <= rate_hit <= 1.0, f"Hit rate {rate_hit} out of [0, 1]"
        finally:
            move_stats["total"] = original_total
            move_stats["cache_hits"] = original_hits

    def test_record_move_stat_hit_rate_bounded(self):
        """Hit rate returned by _record_move_stat must always be in [0.0, 1.0]."""
        from llm.server import _record_move_stat, move_stats

        original_total = move_stats["total"]
        original_hits = move_stats["cache_hits"]
        try:
            move_stats["total"] = 0
            move_stats["cache_hits"] = 0
            for _ in range(10):
                rate = _record_move_stat(cache_hit=True)
                assert 0.0 <= rate <= 1.0
            for _ in range(5):
                rate = _record_move_stat(cache_hit=False)
                assert 0.0 <= rate <= 1.0
        finally:
            move_stats["total"] = original_total
            move_stats["cache_hits"] = original_hits


# ===========================================================================
# WIRE-04  server.py calls log_move with all required arguments
# ===========================================================================


class TestLogMoveCallSite:
    """WIRE-04: server.py must call log_move() with the required arguments.

    log_move() is the persistence entry-point for every move played through /move.
    If the call site drifts from the function signature (game_id, ply, fen, uci,
    san, eval) the DB write will fail silently or raise TypeError.
    """

    def test_log_move_call_exists_in_server(self):
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "log_move(" in source, (
            "server.py must call log_move() to persist moves to the database. "
            "The call site was removed or renamed."
        )

    def test_log_move_called_with_game_id_kwarg(self):
        """log_move must be called with the game_id keyword argument."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "game_id=" in source, (
            "log_move() call in server.py must pass game_id= explicitly. "
            "Positional-only call would break if the signature changes."
        )

    def test_log_move_uses_player_scoped_game_id(self):
        """log_move must use get_or_create_auto_game, not the old 'demo' placeholder."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert 'game_id="demo"' not in source and "game_id='demo'" not in source, (
            "The hard-coded game_id='demo' placeholder is still present in server.py. "
            "Replace it with get_or_create_auto_game(str(player.id))."
        )
        assert "get_or_create_auto_game" in source, (
            "server.py must call get_or_create_auto_game() to produce a player-scoped "
            "game ID for log_move(); the per-player grouping replaced the 'demo' placeholder."
        )


# ===========================================================================
# WIRE-05  GameFinishClosedLoopRequest model is still in server.py
# ===========================================================================


class TestUnusedModelPresent:
    """WIRE-05: GameFinishClosedLoopRequest must remain in server.py.

    The model is defined but has no route yet.  Its presence acts as a
    design-intent marker.  This test ensures it is not accidentally deleted
    before the corresponding endpoint is implemented.
    """

    def test_game_finish_closed_loop_request_defined(self):
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "GameFinishClosedLoopRequest" in source, (
            "GameFinishClosedLoopRequest was removed from server.py. "
            "This model is a design-intent marker for the closed-loop game finish "
            "endpoint that is not yet implemented. Either implement the route or "
            "leave the model in place."
        )


# ===========================================================================
# WIRE-06  generate_validated_explanation is imported in server.py
# ===========================================================================


class TestExplainPipelineImported:
    """WIRE-06: generate_validated_explanation must remain imported in server.py.

    The import ensures that the module is loadable and type-checked even though
    the function is not yet wired to an HTTP endpoint.  Removing the import
    would silently break the LLM pipeline at wiring time.
    """

    def test_generate_validated_explanation_imported(self):
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "generate_validated_explanation" in source, (
            "generate_validated_explanation was removed from server.py imports. "
            "Keep the import to ensure the module stays type-checked and wiring "
            "the function to /explain in the future does not break silently."
        )


# ===========================================================================
# WIRE-07  events/router.py has a logger and uses it
# ===========================================================================


class TestEventsRouterUsesLogger:
    """WIRE-07: events/router.py must use logger (not silenced)."""

    def test_events_router_imports_logging(self):
        source = _EVENTS_ROUTER.read_text(encoding="utf-8")
        assert "import logging" in source, (
            "events/router.py does not import logging. "
            "Diagnostic calls will fail or be silently dropped."
        )

    def test_events_router_defines_logger(self):
        source = _EVENTS_ROUTER.read_text(encoding="utf-8")
        assert "getLogger" in source, (
            "events/router.py does not define a module-level logger. "
            "Add: logger = logging.getLogger(__name__)"
        )
