from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from threading import Lock

import chess
import chess.engine

try:
    from .engine_pool import EnginePool
    from .position_input import normalize_position
except ImportError:
    from engine_pool import EnginePool
    from position_input import normalize_position


class EngineEvaluator:
    def __init__(self, pool: EnginePool):
        self.pool = pool
        default_nodes = os.getenv("ENGINE_DEFAULT_NODES") or os.getenv("ENGINE_NODES") or "5000"
        self.default_nodes = max(1, int(default_nodes))
        self.acquire_timeout_ms = max(0, int(os.getenv("ENGINE_ACQUIRE_TIMEOUT_MS", "50")))
        self.board_cache_size = max(1, int(os.getenv("ENGINE_BOARD_CACHE_SIZE", "1024")))
        self.result_cache_size = max(1, int(os.getenv("ENGINE_RESULT_CACHE_SIZE", "2048")))
        self._board_cache: OrderedDict[str, chess.Board] = OrderedDict()
        self._result_cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_lock = Lock()

    def resolve_limits(
        self,
        *,
        movetime: int | None,
        nodes: int | None,
    ) -> tuple[int | None, int | None]:
        resolved_movetime = None if movetime is None else max(1, int(movetime))
        resolved_nodes = None if nodes is None else max(1, int(nodes))
        if resolved_movetime is None and resolved_nodes is None:
            resolved_nodes = self.default_nodes
        return resolved_movetime, resolved_nodes

    def _cache_key(self, fen: str, movetime: int | None, nodes: int | None) -> str:
        if nodes is not None:
            return f"{fen}:nodes:{nodes}"
        time_key = "none" if movetime is None else movetime
        return f"{fen}:movetime:{time_key}"

    def _get_cached_result(self, key: str) -> dict | None:
        with self._cache_lock:
            cached = self._result_cache.get(key)
            if cached is None:
                return None
            self._result_cache.move_to_end(key)
            return dict(cached)

    def _store_cached_result(self, key: str, value: dict) -> None:
        with self._cache_lock:
            self._result_cache[key] = dict(value)
            self._result_cache.move_to_end(key)
            while len(self._result_cache) > self.result_cache_size:
                self._result_cache.popitem(last=False)

    def _get_board(self, fen: str) -> chess.Board:
        with self._cache_lock:
            cached = self._board_cache.get(fen)
            if cached is not None:
                self._board_cache.move_to_end(fen)
                return cached.copy(stack=False)

        board = chess.Board(fen)
        with self._cache_lock:
            self._board_cache[fen] = board.copy(stack=False)
            self._board_cache.move_to_end(fen)
            while len(self._board_cache) > self.board_cache_size:
                self._board_cache.popitem(last=False)
        return board

    def _remember_board(self, fen: str, board: chess.Board) -> chess.Board:
        with self._cache_lock:
            self._board_cache[fen] = board.copy(stack=False)
            self._board_cache.move_to_end(fen)
            while len(self._board_cache) > self.board_cache_size:
                self._board_cache.popitem(last=False)
        return board.copy(stack=False)

    def _fast_fallback(self, board: chess.Board) -> dict:
        first_move = next(iter(board.legal_moves), None)
        return {"score": None, "best_move": first_move.uci() if first_move else None}

    def _analysis_limit(
        self,
        *,
        movetime: int | None,
        nodes: int | None,
    ) -> chess.engine.Limit:
        if nodes is not None:
            return chess.engine.Limit(nodes=max(1, int(nodes)))
        effective_movetime = 20 if movetime is None else max(1, int(movetime))
        return chess.engine.Limit(time=effective_movetime / 1000.0)

    async def evaluate_with_engine(
        self,
        engine: chess.engine.UciProtocol,
        fen: str | None = None,
        *,
        moves: list[str] | None = None,
        movetime: int | None = None,
        nodes: int | None = None,
        board: chess.Board | None = None,
    ) -> dict:
        movetime, nodes = self.resolve_limits(movetime=movetime, nodes=nodes)
        if board is None:
            position_fen, _, built_board = normalize_position(fen=fen, moves=moves)
            board = self._remember_board(position_fen, built_board)
        else:
            board = self._remember_board(board.fen(), board)
        result = await engine.analyse(
            board,
            self._analysis_limit(movetime=movetime, nodes=nodes),
        )

        score_obj = result.get("score")
        score = score_obj.white().score(mate_score=10000) if score_obj is not None else None
        pv = result.get("pv") or []

        payload = {
            "score": score,
            "best_move": pv[0].uci() if pv else None,
        }
        return payload

    async def evaluate_with_metrics(
        self,
        fen: str | None = None,
        *,
        moves: list[str] | None = None,
        movetime: int | None = None,
        nodes: int | None = None,
    ) -> tuple[dict, dict]:
        position_fen, _, board = normalize_position(fen=fen, moves=moves)
        movetime, nodes = self.resolve_limits(movetime=movetime, nodes=nodes)
        board = self._remember_board(position_fen, board)
        cache_key = self._cache_key(position_fen, movetime, nodes)

        cached = self._get_cached_result(cache_key)
        if cached is not None:
            return cached, {
                "engine_wait_ms": 0.0,
                "engine_eval_ms": 0.0,
                "engine_fallback": False,
                "engine_result_cache_hit": True,
            }

        fallback = self._fast_fallback(board)
        wait_start = time.perf_counter()
        engine = (
            self.pool.try_acquire()
            if hasattr(self.pool, "try_acquire")
            else self.pool.acquire_nowait()
        )
        if engine is None and self.acquire_timeout_ms > 0:
            try:
                engine = await asyncio.wait_for(
                    self.pool.acquire(), timeout=self.acquire_timeout_ms / 1000.0
                )
            except asyncio.TimeoutError:
                wait_ms = round((time.perf_counter() - wait_start) * 1000, 3)
                self._store_cached_result(cache_key, fallback)
                return fallback, {
                    "engine_wait_ms": wait_ms,
                    "engine_eval_ms": 0.0,
                    "engine_fallback": True,
                    "engine_result_cache_hit": False,
                }
        elif engine is None:
            wait_ms = round((time.perf_counter() - wait_start) * 1000, 3)
            self._store_cached_result(cache_key, fallback)
            return fallback, {
                "engine_wait_ms": wait_ms,
                "engine_eval_ms": 0.0,
                "engine_fallback": True,
                "engine_result_cache_hit": False,
            }

        wait_ms = round((time.perf_counter() - wait_start) * 1000, 3)
        eval_start = time.perf_counter()
        try:
            payload = await self.evaluate_with_engine(
                engine,
                fen=position_fen,
                moves=moves,
                movetime=movetime,
                nodes=nodes,
                board=board,
            )
        finally:
            await self.pool.release(engine)
        eval_ms = round((time.perf_counter() - eval_start) * 1000, 3)
        self._store_cached_result(cache_key, payload)
        return payload, {
            "engine_wait_ms": wait_ms,
            "engine_eval_ms": eval_ms,
            "engine_fallback": False,
            "engine_result_cache_hit": False,
        }

    async def evaluate(
        self,
        fen: str | None = None,
        *,
        moves: list[str] | None = None,
        movetime: int | None = None,
        nodes: int | None = None,
    ):
        payload, _ = await self.evaluate_with_metrics(
            fen=fen,
            moves=moves,
            movetime=movetime,
            nodes=nodes,
        )
        return payload
