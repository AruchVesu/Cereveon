"""
Unit tests for ``StockfishEnginePool.evaluate_position`` —
llm/tests/test_engine_pool_evaluate_position.py

Pins the shape contract of the method added in PR #87 so the Mode-1
``/live/move`` route has a stable ``stockfish_json`` to hand into
``extract_engine_signal``.  Without this method, ``/live/move`` was
passing an empty dict to ``extract_engine_signal`` and the engine
signal degraded to a FEN-only heuristic that could not see tactical
threats — the LLM then wrote "solid, balanced" replies regardless of
whether the human had just hung a piece.

The return shape was widened in the ESV-legal Stockfish enrichment to
include deterministically-computed ``tactical_flags`` and
``position_flags`` lists alongside the ``evaluation`` dict (see
``llm/seca/engines/stockfish/board_features.py``).  Tests in this
module assert on the ``evaluation`` sub-dict directly so the
flag-content tests can live in ``test_board_features.py`` without
duplicating coverage here.

The ``FenEvalCache`` cases (EVAL_POS_07+) pin the read-through cache
added to close the /game/finish latency gap: the finish recompute must
be servable from the evaluations /live/move already ran during the
game, and a poisoned or corrupted cache entry must degrade to a miss —
never to a served fake score.

Stable test IDs (do NOT rename):
  EVAL_POS_01  CP score returns evaluation ``{"type": "cp", "value": <int>}`` + flag lists
  EVAL_POS_02  Mate score returns evaluation ``{"type": "mate", "value": <signed_int>}`` + flag lists
  EVAL_POS_03  ``score()`` returning ``None`` (rare engine quirk) defaults to cp=0 + flag lists
  EVAL_POS_04  Engine that has not been started raises RuntimeError
  EVAL_POS_05  Queue exhaustion raises a clear RuntimeError, not queue.Empty
  EVAL_POS_06  Healthy engine is returned to the pool after evaluate_position
  EVAL_POS_07  Cache hit: same position + movetime served without a second analyse
  EVAL_POS_08  Key normalisation: counter / cosmetic-EP FEN spellings share one entry
  EVAL_POS_09  Different movetime_ms is a different cache class (re-analyses)
  EVAL_POS_10  Defensive zero-cp (missing score) is never cached
  EVAL_POS_11  Malformed / out-of-bounds payloads are rejected on read and write
  EVAL_POS_12  Mate evals round-trip through the cache with sign preserved
  EVAL_POS_13  Warmed cache: compute_accuracy_from_pgn runs with zero engine calls
  EVAL_POS_14  Cache-hit result is mutation-insulated (copies, not shared refs)
"""

from __future__ import annotations

import io
import queue
import unittest

import chess
import chess.engine
import chess.pgn

from llm.seca.analysis.pgn_accuracy import compute_accuracy_from_pgn
from llm.seca.engines.stockfish.pool import (
    EnginePoolSettings,
    FenEvalCache,
    StockfishEnginePool,
)

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class _FakeTransport:
    """Stand-in for ``asyncio.SubprocessTransport``; matches the surface
    used by ``StockfishEnginePool._release_engine`` (only ``is_closing``).
    """

    def __init__(self, *, closing: bool = False) -> None:
        self._closing = closing

    def is_closing(self) -> bool:
        return self._closing


class _FakeEngine:
    """SimpleEngine stand-in.  Returns a pre-built ``analyse`` info dict
    so each test can shape the score (cp / mate / missing) without
    needing a real Stockfish binary on PATH.
    """

    def __init__(self, *, analyse_info: dict | None = None) -> None:
        self.transport = _FakeTransport()
        self._analyse_info = analyse_info if analyse_info is not None else {}
        self.analyse_call_count = 0
        self.configure_call_count = 0
        self.quit_called = False

    def analyse(self, board: chess.Board, limit: chess.engine.Limit, **_):  # noqa: ARG002
        self.analyse_call_count += 1
        return self._analyse_info

    def configure(self, options):  # noqa: ARG002
        self.configure_call_count += 1

    def quit(self) -> None:
        self.quit_called = True


def _settings(pool_size: int = 1) -> EnginePoolSettings:
    return EnginePoolSettings(
        stockfish_path="/dev/null",  # never opened — pool is hand-populated
        pool_size=pool_size,
        queue_timeout_ms=50,
    )


def _pool_with(
    *,
    engine: _FakeEngine,
    eval_cache: FenEvalCache | None = None,
) -> StockfishEnginePool:
    """Build a 1-slot pool pre-populated with the given engine.
    Bypasses ``startup()`` so no real binary is needed.
    """
    pool = StockfishEnginePool(_settings(pool_size=1), eval_cache=eval_cache)
    pool._started = True
    pool._engines.put(engine)
    return pool


def _cp_engine(value: int = 42) -> _FakeEngine:
    return _FakeEngine(
        analyse_info={"score": chess.engine.PovScore(chess.engine.Cp(value), chess.WHITE)}
    )


class TestEvaluatePositionShape(unittest.TestCase):
    """Cases EVAL_POS_01..03 — return-shape contract."""

    def test_cp_score_returns_cp_dict(self):
        """EVAL_POS_01."""
        engine = _FakeEngine(
            analyse_info={"score": chess.engine.PovScore(chess.engine.Cp(42), chess.WHITE)}
        )
        pool = _pool_with(engine=engine)

        result = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=50)

        self.assertEqual(result["evaluation"], {"type": "cp", "value": 42})
        self.assertIsInstance(result["tactical_flags"], list)
        self.assertIsInstance(result["position_flags"], list)
        self.assertEqual(engine.analyse_call_count, 1)

    def test_mate_score_returns_mate_dict_with_signed_value(self):
        """EVAL_POS_02.  Mate-in-3 from White's POV → value=3."""
        engine = _FakeEngine(analyse_info={"score": chess.engine.PovScore(
            chess.engine.Mate(3), chess.WHITE
        )})
        pool = _pool_with(engine=engine)

        result = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=50)

        self.assertEqual(result["evaluation"]["type"], "mate")
        self.assertEqual(result["evaluation"]["value"], 3)

    def test_missing_score_defaults_to_zero_cp(self):
        """EVAL_POS_03.  ``info`` without a ``score`` key (defensive
        fallback) should not blow up — return neutral cp=0 so the
        caller still gets a valid ``stockfish_json`` shape and
        ``extract_engine_signal`` tags it as band="equal".  Flag lists
        are still populated because they are computed from the board,
        independent of the engine score.
        """
        engine = _FakeEngine(analyse_info={})  # no score key
        pool = _pool_with(engine=engine)

        result = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=50)

        self.assertEqual(result["evaluation"], {"type": "cp", "value": 0})
        self.assertIsInstance(result["tactical_flags"], list)
        self.assertIsInstance(result["position_flags"], list)


class TestEvaluatePositionLifecycle(unittest.TestCase):
    """Cases EVAL_POS_04..06 — pool-state contract."""

    def test_unstarted_pool_raises_runtime_error(self):
        """EVAL_POS_04.  Calling evaluate_position before startup() must
        raise so the caller doesn't silently get an undefined result.
        """
        pool = StockfishEnginePool(_settings())
        # Note: _started defaults to False; we don't call startup() here.

        with self.assertRaises(RuntimeError):
            pool.evaluate_position(fen=_STARTING_FEN)

    def test_queue_exhaustion_raises_runtime_error(self):
        """EVAL_POS_05.  When the queue is drained, ``queue.Empty`` is
        re-raised as a clear RuntimeError that names the timeout — same
        pattern as ``select_move``.
        """
        pool = StockfishEnginePool(_settings(pool_size=1))
        pool._started = True
        # Queue intentionally empty; the get() call will time out.

        with self.assertRaises(RuntimeError) as ctx:
            pool.evaluate_position(fen=_STARTING_FEN, queue_timeout_ms=10)

        self.assertIn("queue wait exceeded", str(ctx.exception))

    def test_healthy_engine_returned_to_pool(self):
        """EVAL_POS_06.  After a successful evaluate_position the engine
        slot is repopulated so the next caller doesn't time out.
        """
        engine = _FakeEngine(analyse_info={"score": chess.engine.PovScore(
            chess.engine.Cp(0), chess.WHITE
        )})
        pool = _pool_with(engine=engine)

        pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=50)

        # Queue should be back to 1 healthy engine (the same one returned).
        self.assertEqual(pool._engines.qsize(), 1)
        # And a second call should reuse it without raising.
        pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=50)
        self.assertEqual(engine.analyse_call_count, 2)


class _FakeRedis:
    """Minimal sync-redis stand-in: a dict with get/set(ex=...)."""

    def __init__(self, *, canned: bytes | None = None) -> None:
        self._store: dict[str, bytes] = {}
        self._canned = canned

    def get(self, key: str) -> bytes | None:
        if self._canned is not None:
            return self._canned
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self._store[key] = value.encode("utf-8")


class TestEvaluatePositionEvalCache(unittest.TestCase):
    """Cases EVAL_POS_07..12, 14 — FenEvalCache read-through contract."""

    def test_second_call_served_from_cache(self):
        """EVAL_POS_07.  Identical position + movetime → one analyse; the
        hit carries the same evaluation AND freshly-computed flag lists.
        """
        engine = _cp_engine(42)
        pool = _pool_with(engine=engine, eval_cache=FenEvalCache(redis_url=None))

        first = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)
        second = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)

        self.assertEqual(engine.analyse_call_count, 1)
        self.assertEqual(second["evaluation"], {"type": "cp", "value": 42})
        self.assertEqual(first["evaluation"], second["evaluation"])
        self.assertIsInstance(second["tactical_flags"], list)
        self.assertIsInstance(second["position_flags"], list)

    def test_counter_and_ep_spellings_share_one_entry(self):
        """EVAL_POS_08.  The client's live FEN and python-chess's PGN-replay
        FEN can disagree on move counters and on a cosmetic (non-capturable)
        en-passant square; both must land on the same cache entry or the
        /game/finish sweep misses everything it was built to hit.
        """
        engine = _cp_engine(17)
        pool = _pool_with(engine=engine, eval_cache=FenEvalCache(redis_url=None))

        after_e4_client = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
        after_e4_replay = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 5 40"

        pool.evaluate_position(fen=after_e4_client, movetime_ms=200)
        result = pool.evaluate_position(fen=after_e4_replay, movetime_ms=200)

        self.assertEqual(engine.analyse_call_count, 1)
        self.assertEqual(result["evaluation"], {"type": "cp", "value": 17})

    def test_movetime_is_part_of_the_key(self):
        """EVAL_POS_09.  A 150 ms chat eval must not satisfy the 200 ms
        /game/finish budget — the analysis budget IS the quality level.
        """
        engine = _cp_engine()
        pool = _pool_with(engine=engine, eval_cache=FenEvalCache(redis_url=None))

        pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=150)
        pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)

        self.assertEqual(engine.analyse_call_count, 2)

    def test_defensive_zero_score_not_cached(self):
        """EVAL_POS_10.  The missing-score cp=0 fallback is a degraded
        answer; caching it would pin cp=0 for hours on a position the
        engine can genuinely score on the next call.
        """
        engine = _FakeEngine(analyse_info={})  # no score key
        pool = _pool_with(engine=engine, eval_cache=FenEvalCache(redis_url=None))

        pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)
        pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)

        self.assertEqual(engine.analyse_call_count, 2)

    def test_malformed_payloads_rejected_on_read_and_write(self):
        """EVAL_POS_11.  A poisoned or corrupted entry (wrong type, non-int
        value, out-of-bounds score, bool-as-int) must degrade to a miss —
        never be served as an engine score.
        """
        cache = FenEvalCache(redis_url=None)
        board = chess.Board(_STARTING_FEN)

        for bad in (
            {"type": "cp", "value": "e2e4"},
            {"type": "banana", "value": 1},
            {"type": "cp", "value": 10**9},
            {"type": "mate", "value": 10_000},
            {"type": "cp", "value": True},
            {"value": 12},
            "not a dict",
        ):
            cache.set(board=board, movetime_ms=200, evaluation=bad)  # type: ignore[arg-type]
            self.assertIsNone(
                cache.get(board=board, movetime_ms=200),
                f"payload {bad!r} must not round-trip",
            )

        # Poisoned Redis bytes on the read path: shape-invalid JSON falls
        # through to (empty) L1 and reads as a miss.
        poisoned = FenEvalCache(redis_url=None)
        poisoned._redis = _FakeRedis(canned=b'{"type": "cp", "value": "rm -rf /"}')
        self.assertIsNone(poisoned.get(board=board, movetime_ms=200))

        # And a well-formed Redis payload IS served.
        valid = FenEvalCache(redis_url=None)
        valid._redis = _FakeRedis(canned=b'{"type": "cp", "value": 42}')
        self.assertEqual(
            valid.get(board=board, movetime_ms=200),
            {"type": "cp", "value": 42},
        )

    def test_mate_eval_round_trips_with_sign(self):
        """EVAL_POS_12.  Black-mates (negative) must come back negative —
        a sign flip here would grade the game-losing sequence as winning.
        """
        engine = _FakeEngine(
            analyse_info={"score": chess.engine.PovScore(chess.engine.Mate(-2), chess.WHITE)}
        )
        pool = _pool_with(engine=engine, eval_cache=FenEvalCache(redis_url=None))

        pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)
        second = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)

        self.assertEqual(engine.analyse_call_count, 1)
        self.assertEqual(second["evaluation"], {"type": "mate", "value": -2})

    def test_cache_hit_is_mutation_insulated(self):
        """EVAL_POS_14.  Callers mutate the returned stockfish_json (e.g.
        /live/move injects ``errors.last_move_quality``); a mutation must
        not leak into the stored entry or later callers.
        """
        engine = _cp_engine(42)
        pool = _pool_with(engine=engine, eval_cache=FenEvalCache(redis_url=None))

        first = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)
        first["evaluation"]["value"] = 999

        second = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=200)
        self.assertEqual(second["evaluation"], {"type": "cp", "value": 42})


class TestFinishRecomputeRidesTheCache(unittest.TestCase):
    """EVAL_POS_13 — the end-to-end point of the cache: a /game/finish
    accuracy recompute over a game whose positions were already evaluated
    (as /live/move does during play) runs without a single engine call.
    """

    _PGN = '[Event "t"]\n' '[Result "1-0"]\n' "\n" "1. e4 e5 2. Nf3 Nc6 1-0\n"

    def test_warm_cache_recompute_needs_zero_engine_calls(self):
        engine = _cp_engine(30)
        pool = _pool_with(engine=engine, eval_cache=FenEvalCache(redis_url=None))

        # Warm exactly the way live play does: one evaluate_position per
        # mainline position, at the /game/finish budget.
        game = chess.pgn.read_game(io.StringIO(self._PGN))
        assert game is not None
        board = game.board()
        fens = [board.fen()]
        for move in game.mainline_moves():
            board.push(move)
            fens.append(board.fen())
        for fen in fens:
            pool.evaluate_position(fen=fen, movetime_ms=200)
        self.assertEqual(engine.analyse_call_count, len(fens))

        engine.analyse_call_count = 0
        analysis = compute_accuracy_from_pgn(self._PGN, pool, result="win")

        self.assertEqual(engine.analyse_call_count, 0)
        self.assertEqual(analysis.source, "engine")
        self.assertEqual(analysis.moves_analyzed, 2)  # White's e4, Nf3
        # Constant eval → zero centipawn loss → perfect accuracy.
        self.assertEqual(analysis.accuracy, 1.0)


if __name__ == "__main__":  # pragma: no cover - manual runner
    unittest.main()
