"""
LRU cache regression and performance tests for EngineEvaluator.

Invariants pinned by this module
---------------------------------
1. CACHE KEY FORMAT: `_cache_key(fen, movetime, nodes)` produces
   "{fen}:nodes:{n}" when nodes is set, and "{fen}:movetime:{t}"
   when only movetime (or neither) is set.

2. DETERMINISM: The same FEN + limits always produce the same key.

3. FEN ISOLATION: Different FEN strings produce different cache keys.
   A cache hit for position A must never be served for position B.

4. FEN NORMALIZATION: `evaluate_with_metrics` normalizes FEN via
   `normalize_position` before keying the cache. Two call sites that
   pass equivalent positions reach the same cache entry.

5. REAL-ENGINE PATH: After a real engine result is cached (not just
   fallback), subsequent calls for the same position return
   engine_result_cache_hit=True with the original payload intact.

6. LRU EVICTION: When the cache is full and a new entry is inserted,
   the least-recently-used entry is evicted. The MRU entry is retained.

7. CACHE HIT LATENCY: A cache hit completes substantially faster than a
   cold engine evaluation. This is measured with a slow-engine double.

Pool / engine test doubles
--------------------------
_FakeEngine         — instant response; score=42, best_move="e2e4"
_SlowFakeEngine     — simulates a 50 ms engine evaluation; used for the
                      performance latency test.
_TrackingPool       — hands out one _FakeEngine on try_acquire(), then
                      None on subsequent calls; acquire() always raises.
_SlowPool           — hands out one _SlowFakeEngine on try_acquire(),
                      then None; used for latency test.
_EmptyPool          — try_acquire() always None; acquire() raises.
"""

from __future__ import annotations

import asyncio
import time

import chess
import chess.engine
import pytest

try:
    from llm.engine_eval import EngineEvaluator
except ImportError:
    from engine_eval import EngineEvaluator

# Canonical test positions.
_STARTPOS = chess.STARTING_FEN
_FEN_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_FEN_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEngine:
    _SCORE = 42
    _BEST_MOVE = "e2e4"

    async def analyse(self, board, limit, **kwargs):
        move = chess.Move.from_uci(self._BEST_MOVE)
        score = chess.engine.PovScore(chess.engine.Cp(self._SCORE), chess.WHITE)
        return {"score": score, "pv": [move]}

    async def quit(self):
        pass


class _SlowFakeEngine:
    """Simulates a slow engine: each analyse() call sleeps 50 ms."""

    _SCORE = 99
    _BEST_MOVE = "d2d4"
    _SLEEP_S = 0.05

    async def analyse(self, board, limit, **kwargs):
        await asyncio.sleep(self._SLEEP_S)
        move = chess.Move.from_uci(self._BEST_MOVE)
        score = chess.engine.PovScore(chess.engine.Cp(self._SCORE), chess.WHITE)
        return {"score": score, "pv": [move]}

    async def quit(self):
        pass


class _TrackingPool:
    """Yields one _FakeEngine on first try_acquire(), None thereafter."""

    def __init__(self):
        self._engine = _FakeEngine()
        self._calls = 0

    def try_acquire(self):
        self._calls += 1
        return self._engine if self._calls == 1 else None

    async def acquire(self):
        raise NotImplementedError("_TrackingPool.acquire() not needed; LRU hits before pool.")

    async def release(self, engine):
        pass


class _SlowPool:
    """Yields one _SlowFakeEngine on first try_acquire(), None thereafter."""

    def __init__(self):
        self._engine = _SlowFakeEngine()
        self._calls = 0

    def try_acquire(self):
        self._calls += 1
        return self._engine if self._calls == 1 else None

    async def acquire(self):
        raise NotImplementedError("_SlowPool.acquire() not needed; LRU hits before pool.")

    async def release(self, engine):
        pass


class _EmptyPool:
    def try_acquire(self):
        return None

    async def acquire(self):
        raise NotImplementedError("_EmptyPool: no engine available")

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
# 1. Cache key format
# ---------------------------------------------------------------------------


class TestCacheKeyFormat:
    """Pin the string format of EngineEvaluator._cache_key."""

    def setup_method(self):
        self.ev = _make_evaluator(_EmptyPool())

    def test_nodes_key_format(self):
        key = self.ev._cache_key(_STARTPOS, None, 500)
        assert key == f"{_STARTPOS}:nodes:500"

    def test_movetime_key_format(self):
        key = self.ev._cache_key(_STARTPOS, 100, None)
        assert key == f"{_STARTPOS}:movetime:100"

    def test_both_none_uses_none_sentinel(self):
        # movetime=None must produce a distinct key from movetime=0 to prevent
        # false cache hits when the sentinel and explicit-zero collide.
        key = self.ev._cache_key(_STARTPOS, None, None)
        assert key == f"{_STARTPOS}:movetime:none"

    def test_nodes_takes_priority_over_movetime(self):
        # When both are supplied, nodes wins (same as resolve_limits semantics).
        key = self.ev._cache_key(_STARTPOS, 100, 300)
        assert key == f"{_STARTPOS}:nodes:300"

    def test_key_is_deterministic(self):
        a = self.ev._cache_key(_FEN_E4, 50, None)
        b = self.ev._cache_key(_FEN_E4, 50, None)
        assert a == b

    def test_different_fens_produce_different_keys(self):
        k1 = self.ev._cache_key(_STARTPOS, None, 500)
        k2 = self.ev._cache_key(_FEN_E4, None, 500)
        assert k1 != k2

    def test_different_nodes_produce_different_keys(self):
        k1 = self.ev._cache_key(_STARTPOS, None, 100)
        k2 = self.ev._cache_key(_STARTPOS, None, 200)
        assert k1 != k2

    def test_different_movetime_produce_different_keys(self):
        k1 = self.ev._cache_key(_STARTPOS, 20, None)
        k2 = self.ev._cache_key(_STARTPOS, 40, None)
        assert k1 != k2


# ---------------------------------------------------------------------------
# 2. FEN isolation — different positions never share a cache entry
# ---------------------------------------------------------------------------


class TestFenIsolation:

    def test_different_fens_get_separate_cache_entries(self):
        """Cache hit for position A must not be served for position B."""

        async def _run():
            ev = _make_evaluator(_EmptyPool())
            # Populate cache with startpos fallback.
            _, m_start = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
            # First call for a different FEN — must NOT be a cache hit.
            _, m_e4 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=100)
            return m_start, m_e4

        m_start, m_e4 = asyncio.run(_run())

        assert m_start["engine_result_cache_hit"] is False
        assert (
            m_e4["engine_result_cache_hit"] is False
        ), "Cache hit for startpos must not bleed into a different FEN"

    def test_three_positions_each_cache_independently(self):
        """Three distinct FENs each require a fresh evaluation on first call."""

        async def _run():
            ev = _make_evaluator(_EmptyPool())
            _, m1 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
            _, m2 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=100)
            _, m3 = await ev.evaluate_with_metrics(fen=_FEN_E4_E5, nodes=100)
            # Second calls — all three should now be cache hits.
            _, m1b = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
            _, m2b = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=100)
            _, m3b = await ev.evaluate_with_metrics(fen=_FEN_E4_E5, nodes=100)
            return m1, m2, m3, m1b, m2b, m3b

        m1, m2, m3, m1b, m2b, m3b = asyncio.run(_run())

        for m in (m1, m2, m3):
            assert m["engine_result_cache_hit"] is False
        for m in (m1b, m2b, m3b):
            assert m["engine_result_cache_hit"] is True


# ---------------------------------------------------------------------------
# 3. Real-engine cache hit — payload integrity
# ---------------------------------------------------------------------------


class TestRealEngineCacheHit:

    def test_cache_hit_returns_identical_payload(self):
        """Score and best_move from the cached real-engine result match the original."""

        async def _run():
            pool = _TrackingPool()
            ev = _make_evaluator(pool)
            r1, m1 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
            r2, m2 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
            return r1, m1, r2, m2

        r1, m1, r2, m2 = asyncio.run(_run())

        assert m1["engine_result_cache_hit"] is False
        assert m1["engine_fallback"] is False

        assert m2["engine_result_cache_hit"] is True
        assert m2["engine_fallback"] is False

        assert r2["score"] == r1["score"] == _FakeEngine._SCORE
        assert r2["best_move"] == r1["best_move"] == _FakeEngine._BEST_MOVE

    def test_cache_hit_does_not_call_engine_again(self):
        """Pool.try_acquire() is called only once when the second call hits the cache."""

        async def _run():
            pool = _TrackingPool()
            ev = _make_evaluator(pool)
            await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
            await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
            return pool._calls

        calls = asyncio.run(_run())

        # First call: try_acquire() once; second call never reaches the pool.
        assert calls == 1, (
            f"Pool.try_acquire() should be called exactly once (got {calls}). "
            "The second evaluate_with_metrics call must be served by the LRU cache."
        )

    def test_multiple_calls_all_served_from_cache(self):
        """Calls 2–5 for the same position are all served by the LRU cache."""

        async def _run():
            pool = _TrackingPool()
            ev = _make_evaluator(pool)
            results = []
            for _ in range(5):
                r, m = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=200)
                results.append((r, m))
            return results, pool._calls

        results, pool_calls = asyncio.run(_run())

        # First call must not be a cache hit.
        assert results[0][1]["engine_result_cache_hit"] is False
        # Calls 2–5 must all be cache hits.
        for r, m in results[1:]:
            assert m["engine_result_cache_hit"] is True
            assert r["score"] == _FakeEngine._SCORE
        # Pool was only acquired once.
        assert pool_calls == 1


# ---------------------------------------------------------------------------
# 4. FEN normalization — moves= produces same cache entry as FEN=
# ---------------------------------------------------------------------------


class TestFenNormalization:

    def test_moves_list_hits_cache_populated_by_fen(self):
        """
        evaluate_with_metrics normalizes via normalize_position before keying
        the cache. A call with moves=["e2e4"] and a call with the equivalent
        explicit FEN should resolve to the same normalized position and reach
        the same cache slot.
        """

        async def _run():
            pool = _TrackingPool()
            ev = _make_evaluator(pool)
            # First call: supply moves list.
            r1, m1 = await ev.evaluate_with_metrics(fen=_STARTPOS, moves=["e2e4"], nodes=50)
            # Second call: supply the resulting FEN directly.
            r2, m2 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
            return r1, m1, r2, m2

        r1, m1, r2, m2 = asyncio.run(_run())

        assert m1["engine_result_cache_hit"] is False, "First call should be a cold miss"
        assert (
            m2["engine_result_cache_hit"] is True
        ), "FEN equivalent to moves=['e2e4'] must hit the same cache entry"
        assert r2["score"] == r1["score"]


# ---------------------------------------------------------------------------
# 5. LRU eviction — capacity enforcement
# ---------------------------------------------------------------------------


class TestLruEviction:

    def test_lru_entry_is_evicted_when_cache_is_full(self):
        """
        With result_cache_size=2:
          1. Insert STARTPOS → cache=[STARTPOS]
          2. Insert _FEN_E4  → cache=[STARTPOS, _FEN_E4]  (full)
          3. Hit STARTPOS    → promotes STARTPOS to MRU: [_FEN_E4, STARTPOS]
          4. Insert _FEN_E4_E5 → evicts LRU (_FEN_E4): [STARTPOS, _FEN_E4_E5]

        Verify cache state by inspecting _result_cache directly so that
        the verification calls do not re-insert misses and pollute the state.
        """

        async def _run():
            ev = _make_evaluator(_EmptyPool())
            ev.result_cache_size = 2

            await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)  # miss → [STARTPOS]
            await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=100)  # miss → [STARTPOS, FEN_E4]
            await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)  # hit  → [FEN_E4, STARTPOS]
            await ev.evaluate_with_metrics(fen=_FEN_E4_E5, nodes=100)  # miss → evicts FEN_E4

            # Build the expected keys for each position.
            key_start = ev._cache_key(_STARTPOS, None, 100)
            key_e4 = ev._cache_key(_FEN_E4, None, 100)
            key_e4e5 = ev._cache_key(_FEN_E4_E5, None, 100)

            cached_keys = set(ev._result_cache.keys())
            return cached_keys, key_start, key_e4, key_e4e5

        cached_keys, key_start, key_e4, key_e4e5 = asyncio.run(_run())

        assert (
            key_e4 not in cached_keys
        ), "_FEN_E4 was the LRU entry and must be evicted from the cache"
        assert key_start in cached_keys, "STARTPOS was promoted to MRU and must survive eviction"
        assert (
            key_e4e5 in cached_keys
        ), "_FEN_E4_E5 was just inserted and must be present in the cache"

    def test_mru_entry_survives_eviction(self):
        """After filling and then accessing a position, it survives subsequent evictions."""

        async def _run():
            ev = _make_evaluator(_EmptyPool())
            ev.result_cache_size = 1  # cache holds exactly one entry

            await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)  # miss, fills slot
            _, m1 = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)  # hit
            await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=100)  # miss, evicts startpos
            _, m_start_evicted = await ev.evaluate_with_metrics(fen=_STARTPOS, nodes=100)
            return m1, m_start_evicted

        m1, m_start_evicted = asyncio.run(_run())

        assert m1["engine_result_cache_hit"] is True
        assert (
            m_start_evicted["engine_result_cache_hit"] is False
        ), "After inserting a new entry into a size-1 cache, the previous entry is evicted"


# ---------------------------------------------------------------------------
# 6. Cache hit latency — performance regression guard
# ---------------------------------------------------------------------------


class TestCacheHitLatency:

    def test_cache_hit_is_faster_than_cold_evaluation(self):
        """
        A cache hit must be substantially faster than a cold engine call.

        The slow engine double sleeps 50 ms per analyse() call. A cache hit
        short-circuits before reaching the pool so it should complete in well
        under 10 ms.
        """
        COLD_SLEEP_MS = _SlowFakeEngine._SLEEP_S * 1000  # 50 ms
        CACHE_HIT_BUDGET_MS = 10  # generous upper bound for pure Python overhead

        async def _run():
            pool = _SlowPool()
            ev = _make_evaluator(pool)

            # Cold call: engine sleeps 50 ms.
            cold_start = time.perf_counter()
            await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
            cold_ms = (time.perf_counter() - cold_start) * 1000

            # Warm call: hits the LRU cache, no engine contact.
            warm_start = time.perf_counter()
            r2, m2 = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)
            warm_ms = (time.perf_counter() - warm_start) * 1000

            return cold_ms, warm_ms, r2, m2

        cold_ms, warm_ms, r2, m2 = asyncio.run(_run())

        assert m2["engine_result_cache_hit"] is True, "Second call must be a cache hit"
        assert r2["score"] == _SlowFakeEngine._SCORE

        assert cold_ms >= COLD_SLEEP_MS * 0.9, (
            f"Cold call should take at least {COLD_SLEEP_MS * 0.9:.0f} ms "
            f"(slow engine slept {COLD_SLEEP_MS} ms); got {cold_ms:.1f} ms"
        )
        assert warm_ms < CACHE_HIT_BUDGET_MS, (
            f"Cache hit must complete in < {CACHE_HIT_BUDGET_MS} ms; "
            f"got {warm_ms:.2f} ms. The LRU hit path has excessive overhead."
        )

    def test_cache_hit_latency_is_zero_in_metrics(self):
        """
        The metrics dict from a cache hit must report engine_wait_ms=0.0 and
        engine_eval_ms=0.0. These are the documented values for the fast path.
        """

        async def _run():
            pool = _TrackingPool()
            ev = _make_evaluator(pool)
            await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)  # cold
            _, m = await ev.evaluate_with_metrics(fen=_FEN_E4, nodes=50)  # cache hit
            return m

        m = asyncio.run(_run())

        assert m["engine_result_cache_hit"] is True
        assert (
            m["engine_wait_ms"] == 0.0
        ), f"engine_wait_ms must be 0.0 on cache hit, got {m['engine_wait_ms']}"
        assert (
            m["engine_eval_ms"] == 0.0
        ), f"engine_eval_ms must be 0.0 on cache hit, got {m['engine_eval_ms']}"
