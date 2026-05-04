import asyncio
import os
from pathlib import Path

import chess
import llm.opening_book as opening_book_module

from llm.elite_engine_service import EliteEngineService
from llm.opening_book import OpeningBook


class _FakeEvaluator:
    def __init__(self):
        self.default_nodes = 5000
        self.calls = 0

    def resolve_limits(self, *, movetime, nodes):
        if movetime is None and nodes is None:
            return None, self.default_nodes
        return movetime, nodes

    async def evaluate(
        self,
        fen: str | None = None,
        *,
        moves: list[str] | None = None,
        movetime: int | None = None,
        nodes: int | None = None,
    ):
        self.calls += 1
        return {"best_move": "e2e4", "score": 32}


class _FakeBook:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def lookup(self, *, fen: str | None = None, moves: list[str] | None = None):
        self.calls += 1
        return self.payload


def test_opening_book_path_missing_returns_none(tmp_path):
    book = OpeningBook(path=str(tmp_path / "missing.bin"))

    assert book.available is False
    assert book.get_move(chess.STARTING_FEN) is None


def test_opening_book_uses_best_entry_by_default(monkeypatch):
    class _FakeReader:
        def __init__(self):
            self.find_calls = 0
            self.weighted_calls = 0

        def find(self, board):
            self.find_calls += 1
            return type(
                "Entry",
                (),
                {"move": chess.Move.from_uci("e2e4")},
            )()

        def weighted_choice(self, board):
            self.weighted_calls += 1
            return type(
                "Entry",
                (),
                {"move": chess.Move.from_uci("g1f3")},
            )()

        def close(self):
            return None

    reader = _FakeReader()
    monkeypatch.setattr("llm.opening_book.os.path.exists", lambda path: True)
    monkeypatch.setattr("llm.opening_book.chess.polyglot.open_reader", lambda path: reader)

    book = OpeningBook(path="ignored.bin")
    result = book.get_move("startpos")

    assert result == {"best_move": "e2e4", "score": 20, "source": "book"}
    assert reader.find_calls == 1
    assert reader.weighted_calls == 0
    book.close()


def test_opening_book_prefers_repo_root_book_path(monkeypatch):
    class _FakeReader:
        def close(self):
            return None

    expected = str(
        (
            Path(opening_book_module.__file__).resolve().parent.parent / "books" / "performance.bin"
        ).resolve()
    )

    monkeypatch.delenv("OPENING_BOOK_PATH", raising=False)
    monkeypatch.setattr(
        "llm.opening_book.os.path.exists",
        lambda path: os.path.abspath(path) == expected,
    )
    monkeypatch.setattr("llm.opening_book.chess.polyglot.open_reader", lambda path: _FakeReader())

    book = OpeningBook()

    assert book.path == expected
    assert book.available is True
    book.close()


def test_service_prefers_opening_book_before_cache_and_engine(monkeypatch):
    async def _run():
        cached_payloads = []

        async def _unexpected_cache_lookup(
            fen: str | None = None,
            moves: list[str] | None = None,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            raise AssertionError("cache lookup should not run when opening book hits")

        async def _set_cache(
            fen: str | None = None,
            moves: list[str] | None = None,
            *,
            result: dict,
            ttl_seconds: int = 86400,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            cached_payloads.append((fen, moves, result, ttl_seconds, movetime, nodes))

        async def _no_predictions(fen: str, moves: list[str], ttl_seconds: int = 3600):
            return None

        monkeypatch.setattr("llm.elite_engine_service.get_eval_cache", _unexpected_cache_lookup)
        monkeypatch.setattr("llm.elite_engine_service.set_eval_cache", _set_cache)
        monkeypatch.setattr("llm.elite_engine_service.store_predictions", _no_predictions)

        evaluator = _FakeEvaluator()
        book = _FakeBook({"best_move": "e2e4", "score": 20, "source": "book"})
        service = EliteEngineService(evaluator, opening_book=book)

        result = await service.evaluate("startpos")

        assert result == {"best_move": "e2e4", "score": 20, "source": "book"}
        assert evaluator.calls == 0
        assert book.calls == 1
        assert cached_payloads[0][0] == chess.STARTING_FEN
        assert cached_payloads[0][1] == []
        assert cached_payloads[0][5] == evaluator.default_nodes

    asyncio.run(_run())


def test_service_preserves_cached_book_origin(monkeypatch):
    async def _run():
        async def _get_cache(
            fen: str | None = None,
            moves: list[str] | None = None,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            return {"best_move": "e2e4", "score": 20, "source": "book"}

        async def _unexpected_set_cache(
            fen: str | None = None,
            moves: list[str] | None = None,
            *,
            result: dict,
            ttl_seconds: int = 86400,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            raise AssertionError("cache write should not run on cache hit")

        async def _no_predictions(fen: str, moves: list[str], ttl_seconds: int = 3600):
            return None

        monkeypatch.setattr("llm.elite_engine_service.get_eval_cache", _get_cache)
        monkeypatch.setattr("llm.elite_engine_service.set_eval_cache", _unexpected_set_cache)
        monkeypatch.setattr("llm.elite_engine_service.store_predictions", _no_predictions)

        evaluator = _FakeEvaluator()
        book = _FakeBook(None)
        service = EliteEngineService(evaluator, opening_book=book)

        result = await service.evaluate("startpos")

        assert result == {"best_move": "e2e4", "score": 20, "source": "book"}
        assert evaluator.calls == 0
        assert book.calls == 1

    asyncio.run(_run())


def test_service_returns_engine_miss_metrics(monkeypatch):
    async def _run():
        async def _empty_cache(
            fen: str | None = None,
            moves: list[str] | None = None,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            return None

        async def _set_cache(
            fen: str | None = None,
            moves: list[str] | None = None,
            *,
            result: dict,
            ttl_seconds: int = 86400,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            return None

        async def _no_predictions(fen: str, moves: list[str], ttl_seconds: int = 3600):
            return None

        monkeypatch.setattr("llm.elite_engine_service.get_eval_cache", _empty_cache)
        monkeypatch.setattr("llm.elite_engine_service.set_eval_cache", _set_cache)
        monkeypatch.setattr("llm.elite_engine_service.store_predictions", _no_predictions)

        class _MetricsEvaluator:
            default_nodes = 5000

            async def evaluate_with_metrics(
                self,
                fen: str | None = None,
                *,
                moves: list[str] | None = None,
                movetime: int | None = None,
                nodes: int | None = None,
            ):
                return (
                    {"best_move": "e2e4", "score": 24},
                    {
                        "engine_wait_ms": 1.25,
                        "engine_eval_ms": 2.5,
                        "engine_fallback": False,
                        "engine_result_cache_hit": False,
                    },
                )

        evaluator = _MetricsEvaluator()
        book = _FakeBook(None)
        service = EliteEngineService(evaluator, opening_book=book)

        result, metrics = await service.evaluate_with_metrics("startpos")

        assert result == {"best_move": "e2e4", "score": 24, "source": "engine"}
        assert metrics["cache_hit"] is False
        assert metrics["source"] == "engine"
        assert metrics["engine_wait_ms"] == 1.25
        assert metrics["engine_eval_ms"] == 2.5
        assert "total_ms" in metrics

    asyncio.run(_run())


def test_service_supports_moves_input(monkeypatch):
    async def _run():
        async def _get_cache(
            fen: str | None = None,
            moves: list[str] | None = None,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            assert moves == ["e2e4", "e7e5", "g1f3"]
            return None

        async def _set_cache(
            fen: str | None = None,
            moves: list[str] | None = None,
            *,
            result: dict,
            ttl_seconds: int = 86400,
            movetime: int | None = None,
            nodes: int | None = None,
        ):
            assert moves == ["e2e4", "e7e5", "g1f3"]
            return None

        async def _no_predictions(fen: str, moves: list[str], ttl_seconds: int = 3600):
            return None

        monkeypatch.setattr("llm.elite_engine_service.get_eval_cache", _get_cache)
        monkeypatch.setattr("llm.elite_engine_service.set_eval_cache", _set_cache)
        monkeypatch.setattr("llm.elite_engine_service.store_predictions", _no_predictions)

        class _MovesEvaluator:
            default_nodes = 5000

            def resolve_limits(self, *, movetime, nodes):
                return movetime, nodes

            async def evaluate_with_metrics(
                self,
                fen: str | None = None,
                *,
                moves: list[str] | None = None,
                movetime: int | None = None,
                nodes: int | None = None,
            ):
                assert fen is not None
                assert moves == ["e2e4", "e7e5", "g1f3"]
                return {"best_move": "b8c6", "score": 16}, {}

        service = EliteEngineService(_MovesEvaluator(), opening_book=_FakeBook(None))

        result = await service.evaluate(
            moves=["e2e4", "e7e5", "g1f3"],
            movetime=20,
        )

        assert result == {"best_move": "b8c6", "score": 16, "source": "engine"}

    asyncio.run(_run())
