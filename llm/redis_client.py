from __future__ import annotations

import asyncio
import os
from typing import Any

try:
    import redis.asyncio as redis
except ImportError:  # pragma: no cover - optional dependency
    redis: Any | None = None


def _connection_kwargs() -> dict[str, Any]:
    return {
        "decode_responses": True,
        "max_connections": max(1, int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))),
        "socket_connect_timeout": max(
            0.1,
            float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", "1.0")),
        ),
        "socket_timeout": max(0.1, float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "1.0"))),
    }


def _build_client() -> Any | None:
    if redis is None:
        return None

    redis_url = os.getenv("REDIS_URL")
    kwargs = _connection_kwargs()
    if redis_url:
        return redis.from_url(redis_url, **kwargs)

    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    return redis.Redis(host=host, port=port, db=db, **kwargs)


redis_client = _build_client()


async def verify_redis_connection() -> None:
    if redis_client is None:
        raise RuntimeError("Redis client is unavailable. Install the `redis` package.")
    ping_timeout_seconds = max(
        0.1,
        float(os.getenv("REDIS_PING_TIMEOUT_SECONDS", "2.0")),
    )
    try:
        await asyncio.wait_for(redis_client.ping(), timeout=ping_timeout_seconds)
    except Exception as exc:
        raise RuntimeError("Redis ping failed. Check that Redis is running and reachable.") from exc


async def redis_is_available() -> bool:
    if redis_client is None:
        return False
    try:
        await verify_redis_connection()
        return True
    except Exception:
        return False


async def close_redis() -> None:
    if redis_client is None:
        return
    try:
        aclose = getattr(redis_client, "aclose", None)
        if aclose is not None:
            await aclose()
            return
        close = getattr(redis_client, "close", None)
        if close is None:
            return
        maybe_awaitable = close()
        if hasattr(maybe_awaitable, "__await__"):
            await maybe_awaitable
    except Exception:
        pass


def redis_backend_name() -> str:
    if redis_client is None:
        return "disabled"
    return "redis"


async def get_redis_keys(pattern: str = "cc:*") -> list[str]:
    if redis_client is None:
        return []
    try:
        keys = await redis_client.keys(pattern)
        return [key.decode() if isinstance(key, bytes) else key for key in keys]
    except Exception:
        return []


async def get_redis_value(key: str) -> Any | None:
    if redis_client is None:
        return None
    try:
        return await redis_client.get(key)
    except Exception:
        return None


async def get_redis_info(section: str = "stats") -> dict[str, Any]:
    if redis_client is None:
        return {}
    try:
        return await redis_client.info(section)
    except Exception:
        return {}
