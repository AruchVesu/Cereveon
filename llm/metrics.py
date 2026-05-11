from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

# ``MISS_SAMPLES``, ``record_miss_sample``, and ``miss_metrics_snapshot``
# were deleted in the host_app retirement pass (2026-05-12).  They were
# the in-memory miss-tracking surface read by ``GET /debug/miss-metrics``
# on the standalone host_app debug server; nothing in the production
# server.py path called them.  The Prometheus exposition at /metrics
# (Sprint 5.D.1) is the live metrics surface now.


def elapsed_ms(start_time: float) -> float:
    return round((time.perf_counter() - start_time) * 1000, 3)


@contextmanager
def timer(bucket: dict[str, Any], key: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        bucket[key] = elapsed_ms(start)
