from __future__ import annotations

try:
    from . import fen_hash as _fen_hash
    from . import redis_client as _redis_backend
except ImportError:
    import fen_hash as _fen_hash
    import redis_client as _redis_backend


def _pred_key(fen: str) -> str:
    return f"cc:pred:{_fen_hash.fen_hash(fen)}"


async def store_predictions(fen: str, moves: list[str], ttl_seconds: int = 3600) -> None:
    redis_client = _redis_backend.redis_client
    if redis_client is None:
        return

    key = _pred_key(fen)
    try:
        await redis_client.delete(key)
        if moves:
            await redis_client.lpush(key, *moves)
            await redis_client.expire(key, ttl_seconds)
    except Exception:
        return


async def get_predictions(fen: str) -> list[str]:
    redis_client = _redis_backend.redis_client
    if redis_client is None:
        return []

    key = _pred_key(fen)
    try:
        return await redis_client.lrange(key, 0, -1)
    except Exception:
        return []
