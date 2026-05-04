import asyncio

from llm import predictive_cache


class _FakeRedis:
    def __init__(self):
        self.calls = []
        self.range_result = ["e2e4", "d2d4"]
        self.raise_on = None

    async def delete(self, key):
        self.calls.append(("delete", key))
        if self.raise_on == "delete":
            raise RuntimeError("delete failed")

    async def lpush(self, key, *moves):
        self.calls.append(("lpush", key, list(moves)))
        if self.raise_on == "lpush":
            raise RuntimeError("lpush failed")

    async def expire(self, key, ttl_seconds):
        self.calls.append(("expire", key, ttl_seconds))
        if self.raise_on == "expire":
            raise RuntimeError("expire failed")

    async def lrange(self, key, start, end):
        self.calls.append(("lrange", key, start, end))
        if self.raise_on == "lrange":
            raise RuntimeError("lrange failed")
        return list(self.range_result)


def test_store_predictions_writes_moves_and_ttl(monkeypatch):
    async def _run():
        client = _FakeRedis()
        monkeypatch.setattr(predictive_cache._redis_backend, "redis_client", client)

        await predictive_cache.store_predictions("startpos", ["e2e4", "d2d4"], ttl_seconds=42)

        key = predictive_cache._pred_key("startpos")
        assert client.calls == [
            ("delete", key),
            ("lpush", key, ["e2e4", "d2d4"]),
            ("expire", key, 42),
        ]

    asyncio.run(_run())


def test_store_predictions_only_clears_when_moves_are_empty(monkeypatch):
    async def _run():
        client = _FakeRedis()
        monkeypatch.setattr(predictive_cache._redis_backend, "redis_client", client)

        await predictive_cache.store_predictions("startpos", [], ttl_seconds=42)

        assert client.calls == [("delete", predictive_cache._pred_key("startpos"))]

    asyncio.run(_run())


def test_store_predictions_returns_when_redis_is_unavailable(monkeypatch):
    async def _run():
        monkeypatch.setattr(predictive_cache._redis_backend, "redis_client", None)
        await predictive_cache.store_predictions("startpos", ["e2e4"])

    asyncio.run(_run())


def test_store_predictions_swallows_backend_errors(monkeypatch):
    async def _run():
        client = _FakeRedis()
        client.raise_on = "expire"
        monkeypatch.setattr(predictive_cache._redis_backend, "redis_client", client)

        await predictive_cache.store_predictions("startpos", ["e2e4"])

        assert ("expire", predictive_cache._pred_key("startpos"), 3600) in client.calls

    asyncio.run(_run())


def test_get_predictions_returns_cached_moves(monkeypatch):
    async def _run():
        client = _FakeRedis()
        monkeypatch.setattr(predictive_cache._redis_backend, "redis_client", client)

        result = await predictive_cache.get_predictions("startpos")

        assert result == ["e2e4", "d2d4"]
        assert client.calls == [("lrange", predictive_cache._pred_key("startpos"), 0, -1)]

    asyncio.run(_run())


def test_get_predictions_returns_empty_list_on_missing_backend(monkeypatch):
    async def _run():
        monkeypatch.setattr(predictive_cache._redis_backend, "redis_client", None)
        assert await predictive_cache.get_predictions("startpos") == []

    asyncio.run(_run())


def test_get_predictions_swallows_backend_errors(monkeypatch):
    async def _run():
        client = _FakeRedis()
        client.raise_on = "lrange"
        monkeypatch.setattr(predictive_cache._redis_backend, "redis_client", client)

        assert await predictive_cache.get_predictions("startpos") == []

    asyncio.run(_run())
