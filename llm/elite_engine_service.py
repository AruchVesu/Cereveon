from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import chess

try:
    from .engine_eval import EngineEvaluator
    from .metrics import elapsed_ms, timer
    from .opening_book import OpeningBook
    from .position_input import normalize_position
    from .position_cache import get_eval_cache, set_eval_cache
    from .predictive_cache import store_predictions
except ImportError:
    from engine_eval import EngineEvaluator
    from metrics import elapsed_ms, timer
    from opening_book import OpeningBook
    from position_input import normalize_position
    from position_cache import get_eval_cache, set_eval_cache
    from predictive_cache import store_predictions


class EliteEngineService:
    def __init__(self, evaluator: EngineEvaluator, opening_book: OpeningBook | None = None):
        self.evaluator = evaluator
        self.opening_book = opening_book
        self.cache_ttl_s = max(1, int(os.getenv("ENGINE_REDIS_CACHE_TTL_SECONDS", "86400")))
        self.predictive_top_k = max(1, int(os.getenv("ENGINE_PREDICTIVE_TOP_K", "3")))
        self.predictive_nodes = max(
            1,
            int(os.getenv("ENGINE_PREDICTIVE_NODES", str(self.evaluator.default_nodes))),
        )
        self.predictive_movetime = max(1, int(os.getenv("ENGINE_PREDICTIVE_MOVETIME_MS", "20")))

    def _resolve_limits(
        self,
        *,
        movetime: int | None,
        nodes: int | None,
    ) -> tuple[int | None, int | None]:
        if hasattr(self.evaluator, "resolve_limits"):
            return self.evaluator.resolve_limits(movetime=movetime, nodes=nodes)

        resolved_movetime = None if movetime is None else max(1, int(movetime))
        resolved_nodes = None if nodes is None else max(1, int(nodes))
        if resolved_movetime is None and resolved_nodes is None:
            resolved_nodes = max(
                1, int(getattr(self.evaluator, "default_nodes", self.predictive_nodes))
            )
        return resolved_movetime, resolved_nodes

    async def evaluate(
        self,
        fen: str | None = None,
        *,
        moves: list[str] | None = None,
        movetime: int | None = None,
        nodes: int | None = None,
    ) -> dict:
        response, _ = await self.evaluate_with_metrics(
            fen=fen,
            moves=moves,
            movetime=movetime,
            nodes=nodes,
        )
        return response

    async def evaluate_with_metrics(
        self,
        fen: str | None = None,
        *,
        moves: list[str] | None = None,
        movetime: int | None = None,
        nodes: int | None = None,
    ) -> tuple[dict, dict[str, Any]]:
        started = time.perf_counter()
        metrics: dict[str, Any] = {}
        position_fen, normalized_moves, _ = normalize_position(fen=fen, moves=moves)
        movetime, nodes = self._resolve_limits(movetime=movetime, nodes=nodes)

        # Tier 0: Polyglot opening book.
        with timer(metrics, "book_lookup_ms"):
            if self.opening_book is None:
                book_move = None
            elif hasattr(self.opening_book, "lookup"):
                book_move = self.opening_book.lookup(fen=position_fen, moves=normalized_moves)
            else:
                book_move = self.opening_book.get_move(position_fen)
        if book_move is not None:
            with timer(metrics, "cache_store_ms"):
                await set_eval_cache(
                    fen=position_fen,
                    moves=normalized_moves,
                    result=book_move,
                    ttl_seconds=self.cache_ttl_s,
                    movetime=movetime,
                    nodes=nodes,
                )
            metrics["cache_hit"] = True
            metrics["source"] = "book"
            metrics["total_ms"] = elapsed_ms(started)
            return dict(book_move), metrics

        # Tier 1: Redis cache.
        with timer(metrics, "cache_lookup_ms"):
            cached = await get_eval_cache(
                fen=position_fen,
                moves=normalized_moves,
                movetime=movetime,
                nodes=nodes,
            )
        if cached is not None:
            if cached.get("source") == "book":
                response = dict(cached)
            else:
                response = {**cached, "source": "cache"}
            metrics["cache_hit"] = True
            metrics["source"] = response.get("source", "cache")
            metrics["total_ms"] = elapsed_ms(started)
            return response, metrics

        metrics["cache_hit"] = False

        # Tier 2: fast engine.
        if hasattr(self.evaluator, "evaluate_with_metrics"):
            result, evaluator_metrics = await self.evaluator.evaluate_with_metrics(
                fen=position_fen,
                moves=normalized_moves,
                movetime=movetime,
                nodes=nodes,
            )
            metrics.update(evaluator_metrics)
        else:
            # Compatibility path for alternate evaluator implementations.
            eval_started = time.perf_counter()
            result = await self.evaluator.evaluate(
                position_fen,
                moves=normalized_moves,
                movetime=movetime,
                nodes=nodes,
            )
            metrics["engine_wait_ms"] = 0.0
            metrics["engine_eval_ms"] = elapsed_ms(eval_started)
            metrics["engine_fallback"] = False
            metrics["engine_result_cache_hit"] = False

        with timer(metrics, "serialize_ms"):
            response = {**result, "source": "engine"}

        # Cache the fast result.
        with timer(metrics, "cache_store_ms"):
            await set_eval_cache(
                fen=position_fen,
                moves=normalized_moves,
                result=result,
                ttl_seconds=self.cache_ttl_s,
                movetime=movetime,
                nodes=nodes,
            )

        metrics["source"] = "engine"
        metrics["total_ms"] = elapsed_ms(started)

        # Tier 3: async predictive precompute.
        asyncio.create_task(
            self._predict_next_positions(
                fen=position_fen,
                result=result,
                movetime=movetime,
                nodes=nodes,
            )
        )
        return response, metrics

    async def _predict_next_positions(
        self,
        *,
        fen: str,
        result: dict,
        movetime: int | None,
        nodes: int | None,
    ) -> None:
        try:
            board = chess.Board(fen)
            best_move_uci = result.get("best_move")
            if not best_move_uci:
                return

            best_move = chess.Move.from_uci(best_move_uci)
            if best_move not in board.legal_moves:
                return
            board.push(best_move)

            candidates: list[str] = []
            for mv in board.legal_moves:
                candidates.append(mv.uci())
                if len(candidates) >= self.predictive_top_k:
                    break

            await store_predictions(fen, candidates)
            if not candidates:
                return

            # Precompute evals for predicted follow-up positions.
            target_nodes = self.predictive_nodes if nodes is None else max(1, nodes)
            target_movetime = max(1, movetime) if movetime else self.predictive_movetime
            for mv_uci in candidates:
                next_board = chess.Board(board.fen())
                next_board.push_uci(mv_uci)
                next_fen = next_board.fen()
                if await get_eval_cache(
                    fen=next_fen,
                    movetime=target_movetime,
                    nodes=target_nodes,
                ):
                    continue
                followup = await self.evaluator.evaluate(
                    next_fen,
                    movetime=target_movetime,
                    nodes=target_nodes,
                )
                await set_eval_cache(
                    fen=next_fen,
                    result=followup,
                    ttl_seconds=self.cache_ttl_s,
                    movetime=target_movetime,
                    nodes=target_nodes,
                )
        except Exception:
            # Predictive path must never affect request path.
            return
