from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from statistics import mean
from threading import Lock
from typing import Any, Iterator

MISS_SAMPLES: deque[dict[str, Any]] = deque(maxlen=5000)
_MISS_SAMPLES_LOCK = Lock()


def elapsed_ms(start_time: float) -> float:
    return round((time.perf_counter() - start_time) * 1000, 3)


@contextmanager
def timer(bucket: dict[str, Any], key: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        bucket[key] = elapsed_ms(start)


def record_miss_sample(metrics: dict[str, Any]) -> None:
    if metrics.get("cache_hit", False):
        return
    with _MISS_SAMPLES_LOCK:
        MISS_SAMPLES.append(dict(metrics))


def miss_metrics_snapshot() -> dict[str, Any]:
    with _MISS_SAMPLES_LOCK:
        samples = list(MISS_SAMPLES)

    if not samples:
        return {"count": 0}

    def percentile(values: list[float], q: float) -> float:
        ordered = sorted(values)
        index = int(len(ordered) * q)
        index = min(index, len(ordered) - 1)
        return ordered[index]

    total = [float(item.get("total_ms", 0.0)) for item in samples]
    wait = [float(item.get("engine_wait_ms", 0.0)) for item in samples]
    evals = [float(item.get("engine_eval_ms", 0.0)) for item in samples]
    cache = [float(item.get("cache_lookup_ms", 0.0)) for item in samples]
    serialize = [float(item.get("serialize_ms", 0.0)) for item in samples]

    return {
        "count": len(samples),
        "total_avg_ms": round(mean(total), 3),
        "total_p95_ms": round(percentile(total, 0.95), 3),
        "engine_wait_avg_ms": round(mean(wait), 3),
        "engine_wait_p95_ms": round(percentile(wait, 0.95), 3),
        "engine_eval_avg_ms": round(mean(evals), 3),
        "engine_eval_p95_ms": round(percentile(evals, 0.95), 3),
        "cache_lookup_avg_ms": round(mean(cache), 3),
        "serialize_avg_ms": round(mean(serialize), 3),
    }
