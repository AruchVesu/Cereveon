"""
Regression tests for EngineEvaluator.evaluate_with_metrics fallback caching.

Bug pinned by this module
--------------------------
When the engine pool is empty (no worker available), evaluate_with_metrics
falls back to a fast-fallback result (first legal move, score=None) and stores
it in the LRU result cache under the same cache key as a real engine result.

A subsequent call for the *same position and limits* therefore returns
engine_result_cache_hit=True and engine_fallback=False — even though the cached
value originated from the degraded fast-fallback path. The degraded origin
becomes invisible to the caller.

This is not a bug in the cache-hit path itself (the cache is working as
designed), but it is a behavioral invariant that callers must understand:
a cache hit does not imply the cached result came from a real engine run.
The tests below pin this invariant explicitly so that any future change to
tag or segregate fallback-origin entries triggers a review.

Pool test doubles
-----------------
_EmptyPool          — try_acquire() always returns None; acquire() raises
                      NotImplementedError. No engine is ever available.
_TrackingPool       — try_acquire() returns a _FakeEngine on the first call,
                      then None on all subsequent calls. acquire() raises
                      NotImplementedError with a descriptive message (it is not
                      needed for current test scenarios because the second call
                      hits the LRU, but a future test variant that tries to use
                      it should get a clear error rather than AttributeError).
_FakeEngine         — async engine stub returning a real score and best move.
_SlowPool           — try_acquire() returns None, acquire() blocks forever
                      (simulates a pool that times out).
"""

import asyncio
import os

import chess
import chess.engine
import pytest

# Ensure a small acquire timeout so timeout tests finish quickly.
os.environ.setdefault("ENGINE_ACQUIRE_TIMEOUT_MS", "10")

try:
    from llm.engine_eval import EngineEvaluator
except ImportError:
    from engine_eval import EngineEvaluator

# Starting position FEN.
_STARTPOS = chess.STARTING_FEN
# Position after 1. e4
_FEN_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


# ---------------------------------------------------------------------------
# Pool / engine test doubles
# ---------------------------------------------------------------------------


class _FakeEngine:
    """Async engine stub that returns a fixed score and best move."""

    _SCORE = 42
    _BEST_MOVE = "e2e4"

    async def analyse(self, board, limit, **kwargs):
        move = chess.Move.from_uci(self._BEST_MOVE)
        score = chess.engine.PovScore(chess.engine.Cp(self._SCORE), chess.WHITE)
        return {"score": score, "pv": [move]}

    async def quit(self):
        pass


class _EmptyPool:
    """Pool that is always empty — no engine is ever available."""

    def try_acquire(self):
        return None

    async def acquire(self):
        raise NotImplementedError(
            "_EmptyPool.acquire() must not be called in current test scenarios. "
            "The evaluator should fall back immediately when try_acquire returns None "
            "and acquire_timeout_ms is 0."
        )

    async def release(self, engine):
        pass


class _TrackingPool:
    """
    Pool that hands out one _FakeEngine on the first try_acquire() call and
    returns None on all subsequent calls. Simulates a pool that becomes empty
    after the first request.

    acquire() raises NotImplementedError with a descriptive message because
    it is not needed for current test scenarios (the second call hits the LRU
    result cache before reaching the pool acquisition code). If a future test
    variant attempts to call acquire() it will receive a clear error instead
    of AttributeError.
    """

    def __init__(self):
        self._engine = _FakeEngine()
        self._calls = 0

    def try_acquire(self):
        self._calls += 1
        if self._calls == 1:
            return self._engine
        return None

    async def acquire(self):
        raise NotImplementedError(
            "_TrackingPool.acquire() must not be called in current test scenarios. "
            "After the first real evaluation the result is in the LRU cache, so "
            "evaluate_with_metrics returns the cached result before reaching pool "
            "acquisition. If you are writing a test that needs acquire(), use a "
            "different pool double."
        )

    async def release(self, engine):
        pass


class _SlowPool:
    """Pool that is always empty and whose acquire() blocks indefinitely."""

    def try_acquire(self):
        return None

    async def acquire(self):
        # Block forever to trigger asyncio.TimeoutError in the evaluator.
        await asyncio.sleep(9999)

    async def release(self, engine):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_evaluator(pool, *, acquire_timeout_ms: int = 0) -> EngineEvaluator:
    ev = EngineEvaluator(pool)
    ev.acquire_timeout_ms = acquire_timeout_ms
    return ev


# ---------------------------------------------------------------------------
# Test 1: empty pool → fallback on first call
# ---------------------------------------------------------------------------


def test_first_call_empty_pool_returns_fallback():
    """
    When the pool is empty, evaluate_with_metrics must return
    engine_fallback=True and engine_result_cache_hit=False.
    """

    async def _run():
        ev = _make_evaluator(_EmptyPool(), acquire_timeout_ms=0)
        result, metrics = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
        return result, metrics

    result, metrics = asyncio.run(_run())

    assert metrics["engine_fallback"] is True
    assert metrics["engine_result_cache_hit"] is False
    # Fast fallback returns score=None and a best_move (or None if no legal moves).
    assert result["score"] is None


# ---------------------------------------------------------------------------
# Test 2: second call for same position → cache hit, fallback invisible
# ---------------------------------------------------------------------------


def test_second_call_same_position_returns_cache_hit():
    """
    The second call for the same position/limits returns engine_result_cache_hit=True
    and engine_fallback=False — even though the cached value came from the
    degraded fast-fallback path. This pins the 'invisible degraded origin' bug.

    Before any fix: both assertions pass, confirming the bug exists.
    After any fix that tags fallback-origin entries: the second assertion about
    engine_fallback would need to be updated to engine_fallback=True.
    """

    async def _run():
        ev = _make_evaluator(_EmptyPool(), acquire_timeout_ms=0)
        # First call populates the cache with a fallback result.
        await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
        # Second call hits the cache.
        result2, metrics2 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
        return result2, metrics2

    result2, metrics2 = asyncio.run(_run())

    assert (
        metrics2["engine_result_cache_hit"] is True
    ), "Second call for the same position must be a cache hit"
    assert metrics2["engine_fallback"] is False, (
        "Cache hit path always reports engine_fallback=False, even when the cached "
        "value originated from the fast-fallback path. This is the pinned invariant: "
        "a cache hit does not imply the cached result came from a real engine run."
    )


# ---------------------------------------------------------------------------
# Test 3: fallback is cached per limit key
# ---------------------------------------------------------------------------


def test_fallback_cached_per_limit_key():
    """
    Different limit parameters produce different cache keys. A fallback result
    cached for nodes=100 must not serve as a cache hit for nodes=200.
    """

    async def _run():
        ev = _make_evaluator(_EmptyPool(), acquire_timeout_ms=0)
        _, m1 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
        _, m2 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=200)
        _, m3 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
        return m1, m2, m3

    m1, m2, m3 = asyncio.run(_run())

    # First call for nodes=100: fresh fallback.
    assert m1["engine_fallback"] is True
    assert m1["engine_result_cache_hit"] is False

    # First call for nodes=200: different key, fresh fallback.
    assert m2["engine_fallback"] is True
    assert m2["engine_result_cache_hit"] is False

    # Second call for nodes=100: cache hit from earlier.
    assert m3["engine_result_cache_hit"] is True
    assert m3["engine_fallback"] is False


# ---------------------------------------------------------------------------
# Test 4: real engine result is cached and served as cache hit
# ---------------------------------------------------------------------------


def test_real_engine_result_cached_and_returned_as_cache_hit():
    """
    When a real engine result is obtained, it is stored in the LRU cache.
    The second call for the same position returns engine_result_cache_hit=True
    and engine_fallback=False.
    """

    async def _run():
        pool = _TrackingPool()
        ev = _make_evaluator(pool, acquire_timeout_ms=0)
        result1, metrics1 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
        result2, metrics2 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
        return result1, metrics1, result2, metrics2

    result1, metrics1, result2, metrics2 = asyncio.run(_run())

    # First call: real engine, no fallback, no cache hit.
    assert metrics1["engine_fallback"] is False
    assert metrics1["engine_result_cache_hit"] is False
    assert result1["score"] == _FakeEngine._SCORE

    # Second call: cache hit, result identical to first.
    assert metrics2["engine_result_cache_hit"] is True
    assert metrics2["engine_fallback"] is False
    assert result2["score"] == _FakeEngine._SCORE
    assert result2["best_move"] == _FakeEngine._BEST_MOVE


# ---------------------------------------------------------------------------
# Test 5: timeout fallback is also cached
# ---------------------------------------------------------------------------


def test_timeout_fallback_is_cached():
    """
    When acquire() times out, the fallback result is cached. The second call
    for the same position returns engine_result_cache_hit=True.
    """

    async def _run():
        # Use a very short timeout (1 ms) so the test finishes quickly.
        ev = _make_evaluator(_SlowPool(), acquire_timeout_ms=1)
        _, m1 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
        _, m2 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
        return m1, m2

    m1, m2 = asyncio.run(_run())

    assert m1["engine_fallback"] is True
    assert m1["engine_result_cache_hit"] is False

    assert m2["engine_result_cache_hit"] is True
    assert m2["engine_fallback"] is False
