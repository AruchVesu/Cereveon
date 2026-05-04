from __future__ import annotations

import json

try:
    from .cache_keys import eval_cache_key
    from .redis_client import redis_client
except ImportError:
    from cache_keys import eval_cache_key
    from redis_client import redis_client


async def get_eval_cache(
    fen: str | None = None,
    moves: list[str] | None = None,
    movetime: int | None = None,
    nodes: int | None = None,
) -> dict | None:
    if redis_client is None:
        return None

    key = eval_cache_key(fen=fen, moves=moves, movetime_ms=movetime, nodes=nodes)
    try:
        data = await redis_client.get(key)
    except Exception:
        return None

    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


async def set_eval_cache(
    fen: str | None = None,
    moves: list[str] | None = None,
    *,
    result: dict,
    ttl_seconds: int = 86400,
    movetime: int | None = None,
    nodes: int | None = None,
) -> None:
    if redis_client is None:
        return

    key = eval_cache_key(fen=fen, moves=moves, movetime_ms=movetime, nodes=nodes)
    try:
        payload = json.dumps(result, separators=(",", ":"))
        await redis_client.set(key, payload, ex=ttl_seconds)
    except Exception:
        return
