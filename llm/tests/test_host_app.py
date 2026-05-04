import asyncio
from unittest.mock import MagicMock

from llm import host_app
from llm import metrics as metrics_module


def test_engine_raw_bypasses_cached_service_path(monkeypatch):
    async def _run():
        perf_values = iter([10.0, 10.005, 10.006, 10.021, 10.023])

        class _Pool:
            def __init__(self):
                self.released = []

            async def acquire(self):
                return "engine-1"

            async def release(self, engine):
                self.released.append(engine)

        class _Evaluator:
            default_nodes = 5000

            def resolve_limits(self, *, movetime, nodes):
                return movetime, nodes

            def __init__(self):
                self.calls = []

            async def evaluate_with_engine(
                self,
                engine,
                fen: str | None = None,
                *,
                moves: list[str] | None = None,
                movetime: int = 20,
                nodes: int | None = None,
            ):
                self.calls.append((engine, fen, moves, movetime, nodes))
                return {"best_move": "e2e4", "score": 17}

        async def _unexpected(*args, **kwargs):
            raise AssertionError("cached engine service should not run")

        pool = _Pool()
        evaluator = _Evaluator()

        monkeypatch.setattr(host_app.time, "perf_counter", lambda: next(perf_values))
        monkeypatch.setattr(host_app, "engine_pool", pool)
        monkeypatch.setattr(host_app, "engine_eval", evaluator)
        monkeypatch.setattr(host_app.engine_service, "evaluate_with_metrics", _unexpected)

        result = await host_app.engine_raw(
            host_app.EngineEvalRequest(fen="startpos", movetime=20, nodes=4000)
        )

        assert result == {
            "best_move": "e2e4",
            "score": 17,
            "_metrics": {
                "engine_wait_ms": 5.0,
                "engine_eval_ms": 15.0,
                "total_ms": 23.0,
            },
        }
        assert evaluator.calls == [("engine-1", "startpos", [], 20, 4000)]
        assert pool.released == ["engine-1"]

    asyncio.run(_run())


def test_engine_raw_defaults_to_fast_nodes_when_limits_missing(monkeypatch):
    async def _run():
        class _Pool:
            def __init__(self):
                self.released = []

            async def acquire(self):
                return "engine-1"

            async def release(self, engine):
                self.released.append(engine)

        class _Evaluator:
            default_nodes = 3000

            def __init__(self):
                self.calls = []

            def resolve_limits(self, *, movetime, nodes):
                if movetime is None and nodes is None:
                    return None, self.default_nodes
                return movetime, nodes

            async def evaluate_with_engine(
                self,
                engine,
                fen: str | None = None,
                *,
                moves: list[str] | None = None,
                movetime: int | None = None,
                nodes: int | None = None,
            ):
                self.calls.append((engine, fen, moves, movetime, nodes))
                return {"best_move": "e2e4", "score": 17}

        pool = _Pool()
        evaluator = _Evaluator()

        monkeypatch.setattr(host_app, "engine_pool", pool)
        monkeypatch.setattr(host_app, "engine_eval", evaluator)

        result = await host_app.engine_raw(host_app.EngineEvalRequest(fen="startpos"))

        assert result["best_move"] == "e2e4"
        assert evaluator.calls == [("engine-1", "startpos", [], None, 3000)]
        assert pool.released == ["engine-1"]

    asyncio.run(_run())


def test_eval_position_defaults_to_fast_nodes_when_limits_missing(monkeypatch):
    async def _run():
        class _Evaluator:
            default_nodes = 3000

            def resolve_limits(self, *, movetime, nodes):
                if movetime is None and nodes is None:
                    return None, self.default_nodes
                return movetime, nodes

        async def _evaluate_with_metrics(
            *,
            fen: str | None,
            moves: list[str] | None,
            movetime: int | None,
            nodes: int | None,
        ):
            assert fen == "startpos"
            assert moves == []
            assert movetime is None
            assert nodes == 3000
            return (
                {"best_move": "e2e4", "score": 31, "source": "engine"},
                {
                    "cache_hit": False,
                    "source": "engine",
                    "engine_wait_ms": 4.5,
                    "engine_eval_ms": 18.25,
                    "total_ms": 24.0,
                },
            )

        monkeypatch.setattr(host_app._limiter, "enabled", False)
        monkeypatch.setattr(host_app, "engine_eval", _Evaluator())
        monkeypatch.setattr(
            host_app.engine_service, "evaluate_with_metrics", _evaluate_with_metrics
        )

        result = await host_app.eval_position(MagicMock(), host_app.EngineEvalRequest(fen="startpos"))

        assert result["best_move"] == "e2e4"
        assert result["_metrics"]["cache_hit"] is False

    asyncio.run(_run())


def test_eval_position_supports_moves_payload(monkeypatch):
    async def _run():
        class _Evaluator:
            default_nodes = 3000

            def resolve_limits(self, *, movetime, nodes):
                return movetime, nodes

        async def _evaluate_with_metrics(
            *,
            fen: str | None,
            moves: list[str] | None,
            movetime: int | None,
            nodes: int | None,
        ):
            assert fen is None
            assert moves == ["e2e4", "e7e5", "g1f3"]
            assert movetime == 20
            assert nodes is None
            return (
                {"best_move": "b8c6", "score": 22, "source": "book"},
                {"cache_hit": True, "source": "book", "total_ms": 0.8},
            )

        monkeypatch.setattr(host_app._limiter, "enabled", False)
        monkeypatch.setattr(host_app, "engine_eval", _Evaluator())
        monkeypatch.setattr(
            host_app.engine_service, "evaluate_with_metrics", _evaluate_with_metrics
        )

        payload = host_app.EngineEvalRequest(
            moves=["e2e4", "e7e5", "g1f3"],
            movetime_ms=20,
        )
        result = await host_app.eval_position(MagicMock(), payload)

        assert result["best_move"] == "b8c6"
        assert result["_metrics"]["source"] == "book"

    asyncio.run(_run())


def test_evaluate_position_records_miss_metrics(monkeypatch):
    async def _run():
        metrics_module.MISS_SAMPLES.clear()

        async def _evaluate_with_metrics(
            *,
            fen: str | None,
            moves: list[str] | None,
            movetime: int,
            nodes: int | None,
        ):
            assert fen == "startpos"
            assert moves is None
            assert movetime == 20
            assert nodes == 4000
            return (
                {"best_move": "e2e4", "score": 31, "source": "engine"},
                {
                    "cache_hit": False,
                    "source": "engine",
                    "engine_wait_ms": 4.5,
                    "engine_eval_ms": 18.25,
                    "total_ms": 24.0,
                },
            )

        monkeypatch.setattr(
            host_app.engine_service, "evaluate_with_metrics", _evaluate_with_metrics
        )

        result = await host_app._evaluate_position(
            fen="startpos",
            moves=None,
            movetime=20,
            nodes=4000,
        )

        assert result == {
            "best_move": "e2e4",
            "score": 31,
            "source": "engine",
            "_metrics": {
                "cache_hit": False,
                "source": "engine",
                "engine_wait_ms": 4.5,
                "engine_eval_ms": 18.25,
                "total_ms": 24.0,
            },
        }
        assert host_app.debug_miss_metrics() == {
            "count": 1,
            "total_avg_ms": 24.0,
            "total_p95_ms": 24.0,
            "engine_wait_avg_ms": 4.5,
            "engine_wait_p95_ms": 4.5,
            "engine_eval_avg_ms": 18.25,
            "engine_eval_p95_ms": 18.25,
            "cache_lookup_avg_ms": 0.0,
            "serialize_avg_ms": 0.0,
        }

        metrics_module.MISS_SAMPLES.clear()

    asyncio.run(_run())


def test_evaluate_position_does_not_record_cache_hits(monkeypatch):
    async def _run():
        metrics_module.MISS_SAMPLES.clear()

        async def _evaluate_with_metrics(
            *,
            fen: str | None,
            moves: list[str] | None,
            movetime: int,
            nodes: int | None,
        ):
            return (
                {"best_move": "e2e4", "score": 31, "source": "cache"},
                {
                    "cache_hit": True,
                    "source": "cache",
                    "total_ms": 1.25,
                },
            )

        monkeypatch.setattr(
            host_app.engine_service, "evaluate_with_metrics", _evaluate_with_metrics
        )

        result = await host_app._evaluate_position(
            fen="startpos",
            moves=None,
            movetime=20,
            nodes=4000,
        )

        assert result["_metrics"]["cache_hit"] is True
        assert host_app.debug_miss_metrics() == {"count": 0}

        metrics_module.MISS_SAMPLES.clear()

    asyncio.run(_run())


def test_engine_predictions_normalizes_startpos(monkeypatch):
    """The endpoint is now rate-limited; slowapi requires a real Request, so we
    build one with a minimal ASGI scope rather than passing a bare string."""
    from starlette.requests import Request

    async def _run():
        async def _get_predictions(fen: str):
            assert fen == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            return ["e2e4", "d2d4"]

        monkeypatch.setattr(host_app, "get_predictions", _get_predictions)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/engine/predictions",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
        request = Request(scope)
        result = await host_app.engine_predictions(request, "startpos")

        assert result == {
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "predictions": ["e2e4", "d2d4"],
        }

    asyncio.run(_run())
