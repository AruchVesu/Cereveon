"""
FenMoveCache Redis failover tests.

Verifies that FenMoveCache degrades gracefully when Redis is unreachable:
the in-memory LRU cache continues to serve get/set correctly without raising
any exceptions, and cache semantics (TTL eviction, capacity eviction) still hold.

No real Redis instance is started; the unreachable URL triggers a connection
error at construction time, leaving self._redis = None.

Pinned invariants
-----------------
 1. FALLBACK_NO_EXCEPTION:     Constructing FenMoveCache with a bad Redis URL
                                does not raise.
 2. SET_NO_EXCEPTION:          cache.set() with Redis down does not raise.
 3. GET_RETURNS_NONE_MISS:     cache.get() for an unknown key returns None.
 4. SET_THEN_GET_HIT:          After set(), get() returns the stored move.
 5. DIFFERENT_KEYS_ISOLATED:   set() for key A does not affect key B.
 6. TTL_EVICTION:              Items expire after TTL seconds.
 7. CAPACITY_EVICTION:         Oldest item evicted when cache exceeds max_memory_items.
 8. REDIS_CLIENT_IS_NONE:      _redis attribute is None when Redis is unreachable.
 9. SET_OVERWRITE:             A second set() for the same key overwrites the first.
10. LARGE_KEY_SET:             Setting 50 distinct keys does not raise.
"""

from __future__ import annotations

import time

import pytest

from llm.seca.engines.stockfish.pool import FenMoveCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BAD_REDIS_URL = "redis://127.0.0.1:19999"  # port nobody listens on in CI

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_MID_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def _cache(
    *,
    ttl: int = 3600,
    max_items: int = 100,
    redis_url: str = _BAD_REDIS_URL,
) -> FenMoveCache:
    return FenMoveCache(redis_url=redis_url, ttl_seconds=ttl, max_memory_items=max_items)


def _set(cache: FenMoveCache, fen: str, move: str, mode: str = "default") -> None:
    cache.set(fen=fen, mode=mode, movetime_ms=40, target_elo=None, move_uci=move)


def _get(cache: FenMoveCache, fen: str, mode: str = "default") -> str | None:
    return cache.get(fen=fen, mode=mode, movetime_ms=40, target_elo=None)


# ---------------------------------------------------------------------------
# 1. FALLBACK_NO_EXCEPTION
# ---------------------------------------------------------------------------


def test_construction_with_bad_redis_url_does_not_raise():
    """FenMoveCache construction with an unreachable Redis URL must not raise."""
    cache = _cache()  # should not raise
    assert cache is not None


# ---------------------------------------------------------------------------
# 2. SET_NO_EXCEPTION
# ---------------------------------------------------------------------------


def test_set_with_redis_down_does_not_raise():
    """cache.set() with Redis down must not raise."""
    cache = _cache()
    _set(cache, _STARTING_FEN, "e2e4")  # should not raise


# ---------------------------------------------------------------------------
# 3. GET_RETURNS_NONE_MISS
# ---------------------------------------------------------------------------


def test_get_returns_none_on_cache_miss():
    """cache.get() for an unknown key returns None."""
    cache = _cache()
    result = _get(cache, _STARTING_FEN)
    assert result is None, f"Expected None on miss, got {result!r}"


# ---------------------------------------------------------------------------
# 4. SET_THEN_GET_HIT
# ---------------------------------------------------------------------------


def test_set_then_get_returns_move():
    """After set(), get() returns the stored move."""
    cache = _cache()
    _set(cache, _STARTING_FEN, "e2e4")
    result = _get(cache, _STARTING_FEN)
    assert result == "e2e4", f"Expected 'e2e4', got {result!r}"


# ---------------------------------------------------------------------------
# 5. DIFFERENT_KEYS_ISOLATED
# ---------------------------------------------------------------------------


def test_different_fens_are_isolated():
    """set() for one FEN does not affect a different FEN."""
    cache = _cache()
    _set(cache, _STARTING_FEN, "e2e4")
    result = _get(cache, _MID_FEN)
    assert result is None, f"Expected None for untouched key, got {result!r}"


# ---------------------------------------------------------------------------
# 6. TTL_EVICTION
# ---------------------------------------------------------------------------


def test_items_expire_after_ttl():
    """Items stored with ttl_seconds=1 are absent after 1.1 seconds."""
    cache = _cache(ttl=1)
    _set(cache, _STARTING_FEN, "e2e4")
    assert _get(cache, _STARTING_FEN) == "e2e4", "item must be present before TTL"
    time.sleep(1.1)
    result = _get(cache, _STARTING_FEN)
    assert result is None, (
        f"Expected None after TTL expiry, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 7. CAPACITY_EVICTION
# ---------------------------------------------------------------------------


def test_oldest_item_evicted_when_capacity_exceeded():
    """The oldest item is evicted when cache exceeds max_memory_items."""
    cache = _cache(max_items=3)
    fens = [f"fen_{i}" for i in range(4)]
    for i, fen in enumerate(fens):
        cache.set(
            fen=fen,
            mode="default",
            movetime_ms=40,
            target_elo=None,
            move_uci=f"e2e{i + 1}",
        )
    # The first FEN (fens[0]) should have been evicted.
    oldest = cache.get(fen=fens[0], mode="default", movetime_ms=40, target_elo=None)
    assert oldest is None, (
        f"Expected oldest item to be evicted, but got {oldest!r}"
    )
    # The most recent item must still be present.
    newest = cache.get(fen=fens[3], mode="default", movetime_ms=40, target_elo=None)
    assert newest is not None, "Expected newest item to be present after capacity eviction"


# ---------------------------------------------------------------------------
# 8. REDIS_CLIENT_IS_NONE
# ---------------------------------------------------------------------------


def test_redis_client_is_none_when_unreachable():
    """_redis attribute is None when the Redis URL is unreachable."""
    cache = _cache()
    assert cache._redis is None, (
        "_redis must be None when Redis connection fails at construction time"
    )


# ---------------------------------------------------------------------------
# 9. SET_OVERWRITE
# ---------------------------------------------------------------------------


def test_second_set_overwrites_first():
    """A second set() for the same key overwrites the first value."""
    cache = _cache()
    _set(cache, _STARTING_FEN, "e2e4")
    _set(cache, _STARTING_FEN, "d2d4")
    result = _get(cache, _STARTING_FEN)
    assert result == "d2d4", f"Expected overwritten value 'd2d4', got {result!r}"


# ---------------------------------------------------------------------------
# 10. LARGE_KEY_SET
# ---------------------------------------------------------------------------


def test_setting_fifty_distinct_keys_does_not_raise():
    """Setting 50 distinct keys with Redis down does not raise."""
    cache = _cache(max_items=200)
    for i in range(50):
        cache.set(
            fen=f"fen_{i}",
            mode="default",
            movetime_ms=40,
            target_elo=None,
            move_uci="e2e4",
        )  # must not raise
