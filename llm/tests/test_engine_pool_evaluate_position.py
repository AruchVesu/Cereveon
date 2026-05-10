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

Stable test IDs (do NOT rename):
  EVAL_POS_01  CP score returns ``{"evaluation": {"type": "cp", "value": <int>}}``
  EVAL_POS_02  Mate score returns ``{"evaluation": {"type": "mate", "value": <signed_int>}}``
  EVAL_POS_03  ``score()`` returning ``None`` (rare engine quirk) defaults to cp=0
  EVAL_POS_04  Engine that has not been started raises RuntimeError
  EVAL_POS_05  Queue exhaustion raises a clear RuntimeError, not queue.Empty
  EVAL_POS_06  Healthy engine is returned to the pool after evaluate_position
"""

from __future__ import annotations

import queue
import unittest

import chess
import chess.engine

from llm.seca.engines.stockfish.pool import EnginePoolSettings, StockfishEnginePool


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


def _pool_with(*, engine: _FakeEngine) -> StockfishEnginePool:
    """Build a 1-slot pool pre-populated with the given engine.
    Bypasses ``startup()`` so no real binary is needed.
    """
    pool = StockfishEnginePool(_settings(pool_size=1))
    pool._started = True
    pool._engines.put(engine)
    return pool


class TestEvaluatePositionShape(unittest.TestCase):
    """Cases EVAL_POS_01..03 — return-shape contract."""

    def test_cp_score_returns_cp_dict(self):
        """EVAL_POS_01."""
        engine = _FakeEngine(analyse_info={"score": chess.engine.PovScore(
            chess.engine.Cp(42), chess.WHITE
        )})
        pool = _pool_with(engine=engine)

        result = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=50)

        self.assertEqual(result, {"evaluation": {"type": "cp", "value": 42}})
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
        ``extract_engine_signal`` tags it as band="equal".
        """
        engine = _FakeEngine(analyse_info={})  # no score key
        pool = _pool_with(engine=engine)

        result = pool.evaluate_position(fen=_STARTING_FEN, movetime_ms=50)

        self.assertEqual(result, {"evaluation": {"type": "cp", "value": 0}})


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


if __name__ == "__main__":  # pragma: no cover - manual runner
    unittest.main()
