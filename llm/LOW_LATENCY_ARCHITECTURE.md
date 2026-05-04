# Low-Latency Serving Architecture

## High-level flow

Client (Android)  
-> API (`FastAPI`)  
-> Core runtime:
- `FenMoveCache` (L1 in-memory + L2 Redis)
- `StockfishEnginePool` (persistent workers)
- RAG/LLM explainer path
- async predictive cache fill

## Move request lifecycle

1. Request arrives with `fen` (+ optional `mode`, `moves_uci`).
2. Normalize FEN (`startpos` support).
3. Cache lookup first.
   - Hit: return immediately (no engine call).
   - Miss: try engine with guarded queue wait (`ENGINE_QUEUE_TIMEOUT_MS`).
4. If engine unavailable/queue timeout, return fast deterministic fallback move.
5. Return response with telemetry.
6. Async: precompute next likely positions and populate cache.

## Cache model

- Key dimensions: `fen + mode + target_elo + line_key(last move)`.
- L1: bounded in-memory hot cache (`MOVE_CACHE_L1_MAX_ITEMS`).
- L2: Redis with TTL (`MOVE_CACHE_TTL_SECONDS`).
- Startup prewarm for common openings (`ENGINE_PREWARM_FENS`).

## Async predictive layer

- Controlled by:
  - `ENGINE_ASYNC_PREDICT_ENABLED`
  - `ENGINE_ASYNC_PREDICT_PLIES`
  - `ENGINE_ASYNC_PREDICT_MOVETIME_MS`
- Runs after response to avoid user-visible latency.

## Telemetry returned by `/move`

- `latency_ms`
- `engine_time_ms`
- `cache_hit_rate`
- `queue_depth`

## Runtime services

- `api`: FastAPI orchestration
- `redis`: shared cache
- (optional) dedicated worker service for heavier refinement and precompute

