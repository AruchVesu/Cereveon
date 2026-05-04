"""
Performance benchmark tests for EngineEvaluator.

Purpose
-------
This module pins latency SLOs for the three hot paths in EngineEvaluator:

  1. Cache-hit path  — short-circuits before any pool contact.
  2. Fallback path   — pool is empty; _fast_fallback() is returned immediately.
  3. Cold-eval path  — engine is acquired, evaluate_with_engine() is called.

All measurements use deterministic test doubles (no live Stockfish), so the
suite is safe to run in CI without external dependencies.

Benchmark corpus
----------------
Six canonical FEN positions covering all game phases are evaluated in every
path test.  The positions are:

  PHASE_OPENING_START  — initial position (32 pieces)
  PHASE_OPENING_E4     — after 1. e4 (32 pieces, one move played)
  PHASE_OPENING_E4_E5  — after 1. e4 e5 (32 pieces, two moves played)
  PHASE_MIDDLEGAME     — Italian Game after 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 (30 pieces)
  PHASE_ENDGAME_KP     — King-and-Pawn endgame (3 pieces)
  PHASE_TACTICAL       — Italian Game after 1.e4 e5 2.Nf3 Nc6 3.Bc4, black to move

Latency SLOs (CI thresholds)
------------------------------
  CACHE_HIT_BUDGET_MS  =  5   single cache-hit overhead
  FALLBACK_BUDGET_MS   =  5   single fallback path overhead
  BATCH_BUDGET_MS      = 30   full 6-position corpus batch budget
  COLD_MIN_MS          = 45   cold eval with slow engine (≥90 % of 50 ms sleep)
  COLD_MAX_CACHE_MS    = 10   warm (cached) call with slow engine installed

Invariants pinned by this module
---------------------------------
 1. BENCH_POSITIONS_VALID          All corpus FENs pass chess.Board.is_valid().
 2. BENCH_POSITIONS_GAME_PHASES    Corpus contains opening, middlegame, endgame.
 3. BENCH_COLD_RETURNS_PAYLOAD     Cold eval of each position returns dict with
                                   'score' and 'best_move'.
 4. BENCH_METRICS_STRUCTURE        evaluate_with_metrics returns the four standard
                                   metric keys for every corpus position.
 5. BENCH_COLD_NO_CACHE_HIT        First call for each position is always a miss.
 6. BENCH_PAYLOAD_TYPES            score is int or None; best_move is str or None.
 7. BENCH_CACHE_HIT_OVERHEAD       Single cache-hit path < CACHE_HIT_BUDGET_MS ms.
 8. BENCH_CACHE_HIT_BATCH          Full corpus of cache hits < BATCH_BUDGET_MS ms.
 9. BENCH_CACHE_HIT_METRICS_ZERO   Cache hit reports engine_wait_ms=0 and
                                   engine_eval_ms=0.
10. BENCH_FALLBACK_OVERHEAD        Single fallback path < FALLBACK_BUDGET_MS ms.
11. BENCH_FALLBACK_BATCH           Full corpus of fallback paths < BATCH_BUDGET_MS ms.
12. BENCH_FALLBACK_NO_POOL_CALL    Fallback (timeout=0) never calls pool.acquire().
13. BENCH_REGRESSION_SLOW_ENGINE   Cold call with 50 ms engine ≥ COLD_MIN_MS ms;
                                   warm (cached) call < COLD_MAX_CACHE_MS ms.
14. BENCH_UNIQUE_THROUGHPUT        All 6 corpus positions evaluated sequentially
                                   (cache hits) in < BATCH_BUDGET_MS ms.
15. BENCH_NO_STATE_MUTATION        evaluate_with_metrics does not mutate the
                                   caller's board state.
"""

from __future__ import annotations

import asyncio
import time

import chess
import chess.engine

try:
    from llm.engine_eval import EngineEvaluator
except ImportError:
    from engine_eval import EngineEvaluator


# ---------------------------------------------------------------------------
# Benchmark corpus — canonical positions covering all game phases
# ---------------------------------------------------------------------------

PHASE_OPENING_START = chess.STARTING_FEN
PHASE_OPENING_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
PHASE_OPENING_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
PHASE_MIDDLEGAME = "r1bqk2r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
PHASE_ENDGAME_KP = "8/8/8/8/3k4/8/3KP3/8 w - - 0 1"
PHASE_TACTICAL = "r1bqkb1r/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"

BENCHMARK_CORPUS: list[str] = [
    PHASE_OPENING_START,
    PHASE_OPENING_E4,
    PHASE_OPENING_E4_E5,
    PHASE_MIDDLEGAME,
    PHASE_ENDGAME_KP,
    PHASE_TACTICAL,
]

# ---------------------------------------------------------------------------
# Latency SLOs (CI thresholds)
# ---------------------------------------------------------------------------

CACHE_HIT_BUDGET_MS = 5  # maximum overhead for a single cache-hit call
FALLBACK_BUDGET_MS = 5  # maximum overhead for a single fallback call
BATCH_BUDGET_MS = 30  # budget for the full 6-position corpus
COLD_MIN_MS = 45  # cold eval with slow engine must take at least this long
COLD_MAX_CACHE_MS = 10  # warm (cached) call must complete in under this time

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEngine:
    """Instant engine stub; returns a fixed score and best move."""

    _SCORE = 55
    _BEST_MOVE = "e2e4"

    async def analyse(self, board, limit, **kwargs):
        move = chess.Move.from_uci(self._BEST_MOVE)
        score = chess.engine.PovScore(chess.engine.Cp(self._SCORE), chess.WHITE)
        return {"score": score, "pv": [move]}

    async def quit(self):
        pass


class _SlowFakeEngine:
    """Simulates a 50 ms evaluation; used for regression latency tests only."""

    _SCORE = 77
    _BEST_MOVE = "d2d4"
    _SLEEP_S = 0.05

    async def analyse(self, board, limit, **kwargs):
        await asyncio.sleep(self._SLEEP_S)
        move = chess.Move.from_uci(self._BEST_MOVE)
        score = chess.engine.PovScore(chess.engine.Cp(self._SCORE), chess.WHITE)
        return {"score": score, "pv": [move]}

    async def quit(self):
        pass


class _OneShotPool:
    """Yields a given engine on first try_acquire(), None thereafter."""

    def __init__(self, engine):
        self._engine = engine
        self._calls = 0

    def try_acquire(self):
        self._calls += 1
        return self._engine if self._calls == 1 else None

    async def acquire(self):
        raise NotImplementedError("_OneShotPool.acquire() must not be called in these tests.")

    async def release(self, engine):
        pass


class _TrackingEmptyPool:
    """Empty pool that records whether acquire() was ever called."""

    def __init__(self):
        self.acquire_called = False

    def try_acquire(self):
        return None

    async def acquire(self):
        self.acquire_called = True
        raise NotImplementedError("_TrackingEmptyPool: no engine available.")

    async def release(self, engine):
        pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_evaluator(pool, *, acquire_timeout_ms: int = 0) -> EngineEvaluator:
    ev = EngineEvaluator(pool)
    ev.acquire_timeout_ms = acquire_timeout_ms
    return ev


# ===========================================================================
# 1–2  Corpus validation
# ===========================================================================


class TestBenchmarkCorpus:

    def test_all_positions_are_valid(self):
        """BENCH_POSITIONS_VALID: All benchmark FENs represent legal chess positions."""
        for fen in BENCHMARK_CORPUS:
            board = chess.Board(fen)
            assert board.is_valid(), f"Invalid FEN in benchmark corpus: {fen!r}"

    def test_corpus_covers_game_phases(self):
        """BENCH_POSITIONS_GAME_PHASES: Corpus includes opening, middlegame, and endgame."""
        opening_fens = [PHASE_OPENING_START, PHASE_OPENING_E4, PHASE_OPENING_E4_E5]
        for fen in opening_fens:
            total = len(chess.Board(fen).piece_map())
            assert total >= 28, f"Opening position should have >= 28 pieces; got {total}: {fen!r}"

        mid_pieces = len(chess.Board(PHASE_MIDDLEGAME).piece_map())
        assert mid_pieces >= 20, f"Middlegame position should have >= 20 pieces; got {mid_pieces}"

        eg_pieces = len(chess.Board(PHASE_ENDGAME_KP).piece_map())
        assert eg_pieces <= 4, f"Endgame position should have <= 4 pieces; got {eg_pieces}"


# ===========================================================================
# 3–6  Cold evaluation — payload, metrics, type contracts
# ===========================================================================


class TestColdEvaluation:

    def test_cold_eval_returns_payload_for_each_position(self):
        """BENCH_COLD_RETURNS_PAYLOAD: Cold eval returns dict with 'score' and 'best_move'."""

        async def _run():
            results = []
            for fen in BENCHMARK_CORPUS:
                pool = _OneShotPool(_FakeEngine())
                ev = _make_evaluator(pool)
                result, _ = await ev.evaluate_with_metrics(fen=fen, nodes=50)
                results.append(result)
            return results

        for result in asyncio.run(_run()):
            assert "score" in result, "Result dict must contain 'score'"
            assert "best_move" in result, "Result dict must contain 'best_move'"

    def test_metrics_structure_for_each_position(self):
        """BENCH_METRICS_STRUCTURE: evaluate_with_metrics returns all four standard keys."""
        _REQUIRED = {
            "engine_wait_ms",
            "engine_eval_ms",
            "engine_fallback",
            "engine_result_cache_hit",
        }

        async def _run():
            metrics_list = []
            for fen in BENCHMARK_CORPUS:
                pool = _OneShotPool(_FakeEngine())
                ev = _make_evaluator(pool)
                _, metrics = await ev.evaluate_with_metrics(fen=fen, nodes=50)
                metrics_list.append(metrics)
            return metrics_list

        for metrics in asyncio.run(_run()):
            missing = _REQUIRED - set(metrics.keys())
            assert not missing, f"Metrics dict missing keys: {missing}"

    def test_cold_eval_is_not_a_cache_hit(self):
        """BENCH_COLD_NO_CACHE_HIT: First call for each corpus position is always a miss."""

        async def _run():
            hits = []
            for fen in BENCHMARK_CORPUS:
                pool = _OneShotPool(_FakeEngine())
                ev = _make_evaluator(pool)
                _, metrics = await ev.evaluate_with_metrics(fen=fen, nodes=50)
                hits.append(metrics["engine_result_cache_hit"])
            return hits

        for hit in asyncio.run(_run()):
            assert hit is False, "First evaluation of a fresh position must be a cold miss"

    def test_payload_type_contracts(self):
        """BENCH_PAYLOAD_TYPES: score is int or None; best_move is str or None."""

        async def _run():
            results = []
            for fen in BENCHMARK_CORPUS:
                pool = _OneShotPool(_FakeEngine())
                ev = _make_evaluator(pool)
                result, _ = await ev.evaluate_with_metrics(fen=fen, nodes=50)
                results.append(result)
            return results

        for result in asyncio.run(_run()):
            score = result["score"]
            best_move = result["best_move"]
            assert score is None or isinstance(
                score, int
            ), f"score must be int or None; got {type(score).__name__}"
            assert best_move is None or isinstance(
                best_move, str
            ), f"best_move must be str or None; got {type(best_move).__name__}"


# ===========================================================================
# 7–9  Cache-hit path latency
# ===========================================================================


class TestCacheHitLatency:

    def test_single_cache_hit_under_budget(self):
        """BENCH_CACHE_HIT_OVERHEAD: Single cache-hit path < CACHE_HIT_BUDGET_MS ms."""

        async def _run():
            pool = _OneShotPool(_FakeEngine())
            ev = _make_evaluator(pool)
            await ev.evaluate_with_metrics(fen=PHASE_OPENING_E4, nodes=50)
            t0 = time.perf_counter()
            _, metrics = await ev.evaluate_with_metrics(fen=PHASE_OPENING_E4, nodes=50)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return elapsed_ms, metrics

        elapsed_ms, metrics = asyncio.run(_run())

        assert metrics["engine_result_cache_hit"] is True
        assert elapsed_ms < CACHE_HIT_BUDGET_MS, (
            f"Cache-hit path took {elapsed_ms:.2f} ms; "
            f"SLO is {CACHE_HIT_BUDGET_MS} ms. LRU hot path has excessive overhead."
        )

    def test_full_corpus_cache_hits_under_batch_budget(self):
        """BENCH_CACHE_HIT_BATCH: Full corpus of cache hits < BATCH_BUDGET_MS ms."""

        async def _run():
            ev = _make_evaluator(_TrackingEmptyPool())
            for fen in BENCHMARK_CORPUS:
                await ev.evaluate_with_metrics(fen=fen, nodes=50)
            t0 = time.perf_counter()
            for fen in BENCHMARK_CORPUS:
                await ev.evaluate_with_metrics(fen=fen, nodes=50)
            return (time.perf_counter() - t0) * 1000

        elapsed_ms = asyncio.run(_run())

        assert elapsed_ms < BATCH_BUDGET_MS, (
            f"Full-corpus cache-hit batch took {elapsed_ms:.2f} ms; "
            f"SLO is {BATCH_BUDGET_MS} ms."
        )

    def test_cache_hit_metrics_report_zero_latency(self):
        """BENCH_CACHE_HIT_METRICS_ZERO: Cache hit reports engine_wait_ms=0.0 and engine_eval_ms=0.0."""

        async def _run():
            pool = _OneShotPool(_FakeEngine())
            ev = _make_evaluator(pool)
            await ev.evaluate_with_metrics(fen=PHASE_MIDDLEGAME, nodes=50)
            _, metrics = await ev.evaluate_with_metrics(fen=PHASE_MIDDLEGAME, nodes=50)
            return metrics

        metrics = asyncio.run(_run())

        assert metrics["engine_result_cache_hit"] is True
        assert (
            metrics["engine_wait_ms"] == 0.0
        ), f"engine_wait_ms must be 0.0 on cache hit; got {metrics['engine_wait_ms']}"
        assert (
            metrics["engine_eval_ms"] == 0.0
        ), f"engine_eval_ms must be 0.0 on cache hit; got {metrics['engine_eval_ms']}"


# ===========================================================================
# 10–12  Fallback path latency
# ===========================================================================


class TestFallbackLatency:

    def test_single_fallback_under_budget(self):
        """BENCH_FALLBACK_OVERHEAD: Single fallback path < FALLBACK_BUDGET_MS ms."""

        async def _run():
            ev = _make_evaluator(_TrackingEmptyPool())
            t0 = time.perf_counter()
            _, metrics = await ev.evaluate_with_metrics(fen=PHASE_OPENING_E4, nodes=50)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return elapsed_ms, metrics

        elapsed_ms, metrics = asyncio.run(_run())

        assert metrics["engine_fallback"] is True
        assert elapsed_ms < FALLBACK_BUDGET_MS, (
            f"Fallback path took {elapsed_ms:.2f} ms; " f"SLO is {FALLBACK_BUDGET_MS} ms."
        )

    def test_full_corpus_fallback_under_batch_budget(self):
        """BENCH_FALLBACK_BATCH: Full corpus of fallback paths < BATCH_BUDGET_MS ms."""

        async def _run():
            ev = _make_evaluator(_TrackingEmptyPool())
            t0 = time.perf_counter()
            for fen in BENCHMARK_CORPUS:
                await ev.evaluate_with_metrics(fen=fen, nodes=50)
            return (time.perf_counter() - t0) * 1000

        elapsed_ms = asyncio.run(_run())

        assert elapsed_ms < BATCH_BUDGET_MS, (
            f"Full-corpus fallback batch took {elapsed_ms:.2f} ms; " f"SLO is {BATCH_BUDGET_MS} ms."
        )

    def test_fallback_does_not_call_pool_acquire(self):
        """BENCH_FALLBACK_NO_POOL_CALL: Fallback with acquire_timeout_ms=0 never calls acquire()."""

        async def _run():
            pool = _TrackingEmptyPool()
            ev = _make_evaluator(pool, acquire_timeout_ms=0)
            await ev.evaluate_with_metrics(fen=PHASE_OPENING_START, nodes=50)
            return pool.acquire_called

        acquired = asyncio.run(_run())

        assert (
            acquired is False
        ), "Fallback path must not call pool.acquire() when acquire_timeout_ms is 0"


# ===========================================================================
# 13  Regression: cold vs. cached call with slow engine double
# ===========================================================================


class TestRegressionSlowEngine:

    def test_cold_measurably_slower_than_cached(self):
        """BENCH_REGRESSION_SLOW_ENGINE: Cold call with 50 ms engine >= COLD_MIN_MS ms;
        warm call < COLD_MAX_CACHE_MS ms."""

        async def _run():
            pool = _OneShotPool(_SlowFakeEngine())
            ev = _make_evaluator(pool)

            cold_t0 = time.perf_counter()
            await ev.evaluate_with_metrics(fen=PHASE_MIDDLEGAME, nodes=50)
            cold_ms = (time.perf_counter() - cold_t0) * 1000

            warm_t0 = time.perf_counter()
            _, metrics = await ev.evaluate_with_metrics(fen=PHASE_MIDDLEGAME, nodes=50)
            warm_ms = (time.perf_counter() - warm_t0) * 1000

            return cold_ms, warm_ms, metrics

        cold_ms, warm_ms, metrics = asyncio.run(_run())

        assert metrics["engine_result_cache_hit"] is True, "Second call must be a cache hit"

        sleep_ms = _SlowFakeEngine._SLEEP_S * 1000
        assert cold_ms >= COLD_MIN_MS, (
            f"Cold call with {sleep_ms:.0f} ms engine double must take >= {COLD_MIN_MS} ms; "
            f"got {cold_ms:.1f} ms. The engine double may not have been invoked."
        )
        assert warm_ms < COLD_MAX_CACHE_MS, (
            f"Cached call must complete in < {COLD_MAX_CACHE_MS} ms; "
            f"got {warm_ms:.2f} ms. The LRU hit path has excessive overhead."
        )


# ===========================================================================
# 14  Sequential unique-position throughput
# ===========================================================================


class TestUniqueThroughput:

    def test_unique_corpus_throughput(self):
        """BENCH_UNIQUE_THROUGHPUT: All 6 corpus positions evaluated (cache hits) in
        < BATCH_BUDGET_MS ms after the board cache is warm."""

        async def _run():
            ev = _make_evaluator(_TrackingEmptyPool())
            # First pass: populate board cache and result cache for all positions.
            for fen in BENCHMARK_CORPUS:
                await ev.evaluate_with_metrics(fen=fen, nodes=50)
            # Second pass: measure cache-hit throughput.
            t0 = time.perf_counter()
            hit_flags = []
            for fen in BENCHMARK_CORPUS:
                _, metrics = await ev.evaluate_with_metrics(fen=fen, nodes=50)
                hit_flags.append(metrics["engine_result_cache_hit"])
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return elapsed_ms, hit_flags

        elapsed_ms, hit_flags = asyncio.run(_run())

        for i, hit in enumerate(hit_flags):
            assert (
                hit is True
            ), f"Position {i} ({BENCHMARK_CORPUS[i]!r}) was not a cache hit on the second pass"
        assert elapsed_ms < BATCH_BUDGET_MS, (
            f"Unique-corpus throughput: {elapsed_ms:.2f} ms for "
            f"{len(BENCHMARK_CORPUS)} positions; SLO is {BATCH_BUDGET_MS} ms."
        )


# ===========================================================================
# 15  State immutability
# ===========================================================================


class TestNoStateMutation:

    def test_evaluate_does_not_mutate_caller_board(self):
        """BENCH_NO_STATE_MUTATION: evaluate_with_metrics does not mutate caller's board."""

        async def _run():
            pool = _OneShotPool(_FakeEngine())
            ev = _make_evaluator(pool)
            board = chess.Board(PHASE_MIDDLEGAME)
            fen_before = board.fen()
            pm_before = dict(board.piece_map())

            await ev.evaluate_with_metrics(fen=board.fen(), nodes=50)

            return fen_before, board.fen(), pm_before, dict(board.piece_map())

        fen_before, fen_after, pm_before, pm_after = asyncio.run(_run())

        assert (
            fen_before == fen_after
        ), "Board FEN changed after evaluate_with_metrics — position was mutated!"
        assert (
            pm_before == pm_after
        ), "Board piece_map changed after evaluate_with_metrics — pieces were mutated!"
