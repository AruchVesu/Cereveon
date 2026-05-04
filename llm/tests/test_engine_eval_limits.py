import asyncio

import chess
import pytest

from llm.engine_eval import EngineEvaluator


class _FakeEngine:
    def __init__(self):
        self.limits = []
        self.fens = []

    async def analyse(self, board, limit):
        self.limits.append(limit)
        self.fens.append(board.fen())
        return {
            "score": chess.engine.PovScore(chess.engine.Cp(34), chess.WHITE),
            "pv": [chess.Move.from_uci("e2e4")],
        }


def test_evaluate_with_engine_uses_nodes_limit():
    async def _run():
        evaluator = EngineEvaluator(pool=None)
        engine = _FakeEngine()

        result = await evaluator.evaluate_with_engine(
            engine,
            "startpos",
            movetime=20,
            nodes=4000,
        )

        assert result == {"score": 34, "best_move": "e2e4"}
        assert len(engine.limits) == 1
        assert engine.limits[0].nodes == 4000
        assert engine.limits[0].time is None

    asyncio.run(_run())


def test_evaluate_with_engine_uses_movetime_limit_when_nodes_missing():
    async def _run():
        evaluator = EngineEvaluator(pool=None)
        engine = _FakeEngine()

        result = await evaluator.evaluate_with_engine(
            engine,
            "startpos",
            movetime=20,
            nodes=None,
        )

        assert result == {"score": 34, "best_move": "e2e4"}
        assert len(engine.limits) == 1
        assert engine.limits[0].nodes is None
        assert engine.limits[0].time == pytest.approx(0.02)

    asyncio.run(_run())


def test_evaluate_with_engine_defaults_to_fast_nodes_when_limits_missing():
    async def _run():
        evaluator = EngineEvaluator(pool=None)
        engine = _FakeEngine()

        result = await evaluator.evaluate_with_engine(
            engine,
            "startpos",
            movetime=None,
            nodes=None,
        )

        assert result == {"score": 34, "best_move": "e2e4"}
        assert len(engine.limits) == 1
        assert engine.limits[0].nodes == evaluator.default_nodes
        assert engine.limits[0].time is None

    asyncio.run(_run())


def test_cache_key_ignores_movetime_when_nodes_are_present():
    evaluator = EngineEvaluator(pool=None)

    fast_key = evaluator._cache_key("startpos", movetime=20, nodes=3000)
    slower_key = evaluator._cache_key("startpos", movetime=40, nodes=3000)

    assert fast_key == slower_key


def test_evaluate_with_engine_accepts_moves_input():
    async def _run():
        evaluator = EngineEvaluator(pool=None)
        engine = _FakeEngine()

        result = await evaluator.evaluate_with_engine(
            engine,
            moves=["e2e4", "e7e5", "g1f3"],
            movetime=20,
            nodes=None,
        )

        assert result == {"score": 34, "best_move": "e2e4"}
        assert engine.fens[0] == "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"

    asyncio.run(_run())


def test_cached_result_returns_copy():
    evaluator = EngineEvaluator(pool=None)
    key = evaluator._cache_key("startpos", movetime=None, nodes=evaluator.default_nodes)
    evaluator._store_cached_result(key, {"score": 12, "best_move": "e2e4"})

    cached = evaluator._get_cached_result(key)
    assert cached == {"score": 12, "best_move": "e2e4"}

    cached["score"] = 99

    assert evaluator._get_cached_result(key) == {"score": 12, "best_move": "e2e4"}


def test_store_cached_result_evicts_oldest_entry():
    evaluator = EngineEvaluator(pool=None)
    evaluator.result_cache_size = 1

    evaluator._store_cached_result("first", {"score": 1})
    evaluator._store_cached_result("second", {"score": 2})

    assert evaluator._get_cached_result("first") is None
    assert evaluator._get_cached_result("second") == {"score": 2}


def test_get_board_returns_isolated_copy():
    evaluator = EngineEvaluator(pool=None)

    board_a = evaluator._get_board(chess.STARTING_FEN)
    board_a.push_uci("e2e4")

    board_b = evaluator._get_board(chess.STARTING_FEN)

    assert board_b.fen() == chess.STARTING_FEN


def test_remember_board_evicts_oldest_board():
    evaluator = EngineEvaluator(pool=None)
    evaluator.board_cache_size = 1

    board_a = chess.Board()
    evaluator._remember_board("a", board_a)
    evaluator._remember_board("b", board_a)

    assert "a" not in evaluator._board_cache
    assert "b" in evaluator._board_cache


def test_fast_fallback_returns_first_legal_move():
    evaluator = EngineEvaluator(pool=None)
    payload = evaluator._fast_fallback(chess.Board())

    assert payload["score"] is None
    assert payload["best_move"] == "g1h3"


def test_fast_fallback_handles_positions_without_legal_moves():
    evaluator = EngineEvaluator(pool=None)
    board = chess.Board("7k/5Q2/7K/8/8/8/8/8 b - - 0 1")

    assert evaluator._fast_fallback(board) == {"score": None, "best_move": None}


def test_analysis_limit_defaults_to_fast_movetime():
    evaluator = EngineEvaluator(pool=None)
    limit = evaluator._analysis_limit(movetime=None, nodes=None)

    assert limit.nodes is None
    assert limit.time == pytest.approx(0.02)


def test_resolve_limits_clamps_invalid_values():
    evaluator = EngineEvaluator(pool=None)

    movetime, nodes = evaluator.resolve_limits(movetime=0, nodes=-5)

    assert movetime == 1
    assert nodes == 1


def test_evaluate_with_metrics_returns_cached_payload():
    async def _run():
        class _Pool:
            def try_acquire(self):
                raise AssertionError("engine acquisition should not happen on cache hit")

        evaluator = EngineEvaluator(pool=_Pool())
        key = evaluator._cache_key(chess.STARTING_FEN, None, evaluator.default_nodes)
        evaluator._store_cached_result(key, {"score": 27, "best_move": "e2e4"})

        payload, metrics = await evaluator.evaluate_with_metrics(fen="startpos")

        assert payload == {"score": 27, "best_move": "e2e4"}
        assert metrics["engine_result_cache_hit"] is True
        assert metrics["engine_eval_ms"] == 0.0

    asyncio.run(_run())


def test_evaluate_with_metrics_uses_immediate_fallback_when_pool_is_empty():
    async def _run():
        class _Pool:
            def acquire_nowait(self):
                return None

        evaluator = EngineEvaluator(pool=_Pool())
        evaluator.acquire_timeout_ms = 0

        payload, metrics = await evaluator.evaluate_with_metrics(fen="startpos", movetime=20)

        assert payload["best_move"] == "g1h3"
        assert metrics["engine_fallback"] is True
        assert metrics["engine_result_cache_hit"] is False

    asyncio.run(_run())


def test_evaluate_with_metrics_times_out_waiting_for_engine():
    async def _run():
        class _Pool:
            def try_acquire(self):
                return None

            async def acquire(self):
                await asyncio.sleep(0.02)
                return "late-engine"

        evaluator = EngineEvaluator(pool=_Pool())
        evaluator.acquire_timeout_ms = 1

        payload, metrics = await evaluator.evaluate_with_metrics(fen="startpos", movetime=20)

        assert payload["best_move"] == "g1h3"
        assert metrics["engine_fallback"] is True
        assert metrics["engine_result_cache_hit"] is False

    asyncio.run(_run())


def test_evaluate_with_metrics_uses_pool_engine_and_releases_it():
    async def _run():
        class _Pool:
            def __init__(self):
                self.released = []

            def try_acquire(self):
                return engine

            async def release(self, item):
                self.released.append(item)

        evaluator = EngineEvaluator(pool=_Pool())
        payload, metrics = await evaluator.evaluate_with_metrics(
            fen="startpos",
            movetime=20,
            nodes=4000,
        )

        assert payload == {"score": 34, "best_move": "e2e4"}
        assert metrics["engine_fallback"] is False
        assert evaluator.pool.released == [engine]

    engine = _FakeEngine()
    asyncio.run(_run())


def test_evaluate_returns_payload_without_metrics():
    async def _run():
        evaluator = EngineEvaluator(pool=None)

        async def _evaluate_with_metrics(**kwargs):
            assert kwargs["fen"] == "startpos"
            return {"score": 12, "best_move": "e2e4"}, {"engine_result_cache_hit": False}

        evaluator.evaluate_with_metrics = _evaluate_with_metrics
        payload = await evaluator.evaluate(fen="startpos")

        assert payload == {"score": 12, "best_move": "e2e4"}

    asyncio.run(_run())
