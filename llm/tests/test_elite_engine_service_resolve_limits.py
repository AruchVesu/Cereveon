"""
Regression tests for EliteEngineService._resolve_limits.

Architecture gap documented here
----------------------------------
EliteEngineService._resolve_limits has two code paths:

DELEGATION PATH (evaluator has resolve_limits):
  Both nodes and movetime are passed to evaluator.resolve_limits(), which
  applies max(1, int(...)) floor clamping AND any upper ceiling defined on
  the evaluator (e.g. EngineEvaluator clamps movetime and nodes to its
  configured maxima via the same resolve_limits method).

FALLBACK PATH (evaluator lacks resolve_limits):
  Only max(1, int(...)) floor clamping is applied. There is NO upper ceiling.
  A caller can pass nodes=999_999 and receive nodes=999_999 back unchanged.
  This is an architecture gap: callers on the fallback path bypass the limit
  guardrails that are enforced on the delegation path.

  This gap is intentional for compatibility (alternate evaluator implementations
  may not have resolve_limits), but it must be explicitly documented so that
  anyone adding a new evaluator implementation that lacks resolve_limits
  understands they are opting out of the ceiling guardrails.

  The test test_fallback_path_no_upper_ceiling pins this gap with an assertion
  that nodes == 999_999 to make it visible and reviewable.
"""

import pytest

try:
    from llm.elite_engine_service import EliteEngineService
except ImportError:
    from elite_engine_service import EliteEngineService


# ---------------------------------------------------------------------------
# Evaluator stubs
# ---------------------------------------------------------------------------


class _EvaluatorWithResolveLimits:
    """
    Evaluator stub that exposes resolve_limits with a ceiling of
    max_nodes=10_000 and max_movetime=500 ms.
    """

    default_nodes = 5000

    def resolve_limits(self, *, movetime, nodes):
        resolved_movetime = None if movetime is None else max(1, int(movetime))
        resolved_nodes = None if nodes is None else max(1, int(nodes))
        if resolved_movetime is None and resolved_nodes is None:
            resolved_nodes = self.default_nodes
        # Apply upper ceilings.
        if resolved_nodes is not None:
            resolved_nodes = min(resolved_nodes, 10_000)
        if resolved_movetime is not None:
            resolved_movetime = min(resolved_movetime, 500)
        return resolved_movetime, resolved_nodes


class _EvaluatorWithoutResolveLimits:
    """
    Evaluator stub that does NOT expose resolve_limits. Triggers the fallback
    path in EliteEngineService._resolve_limits.
    """

    default_nodes = 5000
    predictive_nodes = 5000


def _make_service_with(evaluator) -> EliteEngineService:
    # Bypass opening_book and redis dependencies — we only test _resolve_limits.
    service = object.__new__(EliteEngineService)
    service.evaluator = evaluator
    service.opening_book = None
    service.cache_ttl_s = 86400
    service.predictive_top_k = 3
    service.predictive_nodes = getattr(evaluator, "predictive_nodes", 5000)
    service.predictive_movetime = 20
    return service


# ---------------------------------------------------------------------------
# DELEGATION PATH tests
# ---------------------------------------------------------------------------


def test_delegation_path_nodes_clamped_to_evaluator_max():
    """
    When the evaluator has resolve_limits, an over-limit nodes value is
    clamped to the evaluator's maximum (10_000 in the stub).
    """
    service = _make_service_with(_EvaluatorWithResolveLimits())
    _, nodes = service._resolve_limits(movetime=None, nodes=50_000)
    assert nodes == 10_000, f"Expected nodes clamped to evaluator max 10_000, got {nodes}"


def test_delegation_path_movetime_clamped_to_evaluator_max():
    """
    When the evaluator has resolve_limits, an over-limit movetime is clamped
    to the evaluator's maximum (500 ms in the stub).
    """
    service = _make_service_with(_EvaluatorWithResolveLimits())
    movetime, _ = service._resolve_limits(movetime=9999, nodes=None)
    assert movetime == 500, f"Expected movetime clamped to evaluator max 500, got {movetime}"


def test_delegation_path_floor_clamping_nodes():
    """Zero and negative nodes are floored to 1 on the delegation path."""
    service = _make_service_with(_EvaluatorWithResolveLimits())
    _, nodes_zero = service._resolve_limits(movetime=None, nodes=0)
    _, nodes_neg = service._resolve_limits(movetime=None, nodes=-100)
    assert nodes_zero == 1
    assert nodes_neg == 1


def test_delegation_path_floor_clamping_movetime():
    """Zero and negative movetime are floored to 1 on the delegation path."""
    service = _make_service_with(_EvaluatorWithResolveLimits())
    mt_zero, _ = service._resolve_limits(movetime=0, nodes=100)
    mt_neg, _ = service._resolve_limits(movetime=-50, nodes=100)
    assert mt_zero == 1
    assert mt_neg == 1


def test_delegation_path_default_nodes_when_both_none():
    """
    When both movetime and nodes are None, the delegation path falls back to
    the evaluator's default_nodes (5000 in the stub, after ceiling applied).
    """
    service = _make_service_with(_EvaluatorWithResolveLimits())
    movetime, nodes = service._resolve_limits(movetime=None, nodes=None)
    assert movetime is None
    assert nodes == 5000


# ---------------------------------------------------------------------------
# FALLBACK PATH tests (evaluator lacks resolve_limits)
# ---------------------------------------------------------------------------


def test_fallback_path_nodes_not_clamped_above():
    """
    On the fallback path, nodes is NOT subject to an upper ceiling.
    A large nodes value is returned unchanged (floor is still applied).
    """
    service = _make_service_with(_EvaluatorWithoutResolveLimits())
    _, nodes = service._resolve_limits(movetime=None, nodes=50_000)
    assert nodes == 50_000, f"Fallback path must not impose an upper ceiling on nodes. Got {nodes}"


def test_fallback_path_no_upper_ceiling():
    """
    Architecture gap pin: nodes=999_999 is returned exactly as-is on the
    fallback path. This is a documented intentional gap — the fallback path
    does not enforce the upper limit guardrails that the delegation path does.

    If this assertion fails after a code change it means the fallback path
    now applies an upper ceiling. That is a valid improvement, but it must be
    reviewed to ensure backward compatibility with alternate evaluator
    implementations that depend on unclamped limits.
    """
    service = _make_service_with(_EvaluatorWithoutResolveLimits())
    _, nodes = service._resolve_limits(movetime=None, nodes=999_999)
    assert nodes == 999_999, (
        "Architecture gap: fallback path applies no upper ceiling. "
        f"Expected nodes=999_999 (unchanged), got {nodes}. "
        "If you have added ceiling enforcement to the fallback path, update this "
        "test and document the change in .claude/context/engine.md."
    )


def test_fallback_path_floor_clamping_nodes():
    """Zero and negative nodes are floored to 1 on the fallback path."""
    service = _make_service_with(_EvaluatorWithoutResolveLimits())
    _, nodes_zero = service._resolve_limits(movetime=None, nodes=0)
    _, nodes_neg = service._resolve_limits(movetime=None, nodes=-1)
    assert nodes_zero == 1
    assert nodes_neg == 1


def test_fallback_path_floor_clamping_movetime():
    """Zero and negative movetime are floored to 1 on the fallback path."""
    service = _make_service_with(_EvaluatorWithoutResolveLimits())
    mt_zero, _ = service._resolve_limits(movetime=0, nodes=100)
    mt_neg, _ = service._resolve_limits(movetime=-10, nodes=100)
    assert mt_zero == 1
    assert mt_neg == 1


def test_fallback_path_default_nodes_when_both_none():
    """
    When both movetime and nodes are None, the fallback path uses
    evaluator.default_nodes (5000 in the stub) as the default.
    """
    service = _make_service_with(_EvaluatorWithoutResolveLimits())
    movetime, nodes = service._resolve_limits(movetime=None, nodes=None)
    assert movetime is None
    assert nodes == 5000


def test_fallback_path_movetime_not_clamped_above():
    """
    On the fallback path, movetime is NOT subject to an upper ceiling either.
    A large movetime value is returned unchanged.
    """
    service = _make_service_with(_EvaluatorWithoutResolveLimits())
    movetime, _ = service._resolve_limits(movetime=99_999, nodes=None)
    assert movetime == 99_999


# ---------------------------------------------------------------------------
# Both paths: None passthrough
# ---------------------------------------------------------------------------


def test_delegation_path_none_nodes_with_movetime():
    """nodes stays None when movetime is provided and nodes is not (delegation)."""
    service = _make_service_with(_EvaluatorWithResolveLimits())
    movetime, nodes = service._resolve_limits(movetime=100, nodes=None)
    assert movetime == 100
    assert nodes is None


def test_fallback_path_none_nodes_with_movetime():
    """nodes stays None when movetime is provided and nodes is not (fallback)."""
    service = _make_service_with(_EvaluatorWithoutResolveLimits())
    movetime, nodes = service._resolve_limits(movetime=100, nodes=None)
    assert movetime == 100
    assert nodes is None
