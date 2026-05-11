"""Prometheus metrics surface for the Cereveon backend.

Lives outside ``llm.seca.*`` on purpose: the SECA freeze-guard keyword
scan rejects modules whose source contains adaptive-learning tripwires
like the dot-update substring or the train-call substring.  Putting the
metrics layer here keeps that scan focused on the SECA tree without
forcing prometheus_client API names through awkward indirection.

Wiring
------
* HTTP requests   -> ``track_http_request`` middleware in server.py
* Engine pool     -> ``register_engine_pool_collectors`` (callback-based
                     so the pool's hot path stays untouched).
* Auth outcomes   -> ``auth_login_total`` / ``auth_register_total`` from
                     llm.seca.auth.router on success/failure branches.

Exposition
----------
``get_metrics_text()`` returns the wire-format bytes; the /metrics
route in server.py wraps it with ``Response(content_type=METRICS_CONTENT_TYPE)``.
"""

from __future__ import annotations

import logging
from typing import Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants exposed to the route
# ---------------------------------------------------------------------------

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST


# ---------------------------------------------------------------------------
# HTTP metrics
# ---------------------------------------------------------------------------

# Path template is the FastAPI route pattern (e.g. ``/auth/me``,
# ``/players/{player_id}``), not the expanded URL — bounded by route count
# so cardinality stays sane.  Falls back to ``unmatched`` for 404s and
# any branch that bypasses routing.
http_requests_total = Counter(
    "chesscoach_http_requests_total",
    "Count of HTTP requests handled by the FastAPI app.",
    labelnames=("method", "path_template", "status"),
)

http_request_duration_seconds = Histogram(
    "chesscoach_http_request_duration_seconds",
    "Wall-clock duration of HTTP request handling, in seconds.",
    labelnames=("method", "path_template", "status"),
    # Buckets tuned for a mix of fast (/health < 10 ms) and slow
    # (LLM-coaching ~5 s) endpoints.  Default Prom buckets stop at
    # 10 s which would compress all coaching tail latency into +Inf.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


# ---------------------------------------------------------------------------
# Auth metrics
# ---------------------------------------------------------------------------

# ``result`` is one of: success, invalid_credentials, rate_limited,
# server_error.  ``register`` and ``login`` are separate counters since
# their failure modes diverge (register can fail on duplicate email,
# login can't).
auth_login_total = Counter(
    "chesscoach_auth_login_total",
    "Login attempts on /auth/login, labeled by outcome.",
    labelnames=("result",),
)

auth_register_total = Counter(
    "chesscoach_auth_register_total",
    "Registration attempts on /auth/register, labeled by outcome.",
    labelnames=("result",),
)


# ---------------------------------------------------------------------------
# Engine pool metrics
# ---------------------------------------------------------------------------

# Engine pool gauges are populated via callback at scrape time so the
# acquire/release hot path stays free of Prom overhead.  The callback
# reads from a registered provider (``register_engine_pool_provider``)
# rather than importing the pool here, which would create a circular
# import (server.py owns the pool global, server.py imports observability).
_engine_pool_provider: Callable[[], dict[str, int]] | None = None


def register_engine_pool_provider(provider: Callable[[], dict[str, int]]) -> None:
    """Register a callable that returns ``{size, available, in_use}``.

    Called once from server.py lifespan after the pool boots.  The
    provider is invoked at every /metrics scrape; the lock-free reads
    on the pool's internal state are safe because the gauges are an
    observability snapshot, not a control signal.
    """
    global _engine_pool_provider  # pylint: disable=global-statement
    _engine_pool_provider = provider


def _read_pool_stat(key: str) -> float:
    if _engine_pool_provider is None:
        return 0.0
    try:
        return float(_engine_pool_provider().get(key, 0))
    except Exception:  # pylint: disable=broad-except
        logger.exception("engine pool provider raised; returning 0 for %s", key)
        return 0.0


engine_pool_size = Gauge(
    "chesscoach_engine_pool_size",
    "Configured Stockfish engine pool size.",
)
engine_pool_size.set_function(lambda: _read_pool_stat("size"))

engine_pool_available = Gauge(
    "chesscoach_engine_pool_available",
    "Engines currently sitting in the pool, available for acquire.",
)
engine_pool_available.set_function(lambda: _read_pool_stat("available"))

engine_pool_in_use = Gauge(
    "chesscoach_engine_pool_in_use",
    "Engines currently checked out (size minus available).",
)
engine_pool_in_use.set_function(lambda: _read_pool_stat("in_use"))


# ---------------------------------------------------------------------------
# Exposition
# ---------------------------------------------------------------------------


def get_metrics_text() -> bytes:
    """Return the Prometheus wire-format payload for the default registry."""
    return generate_latest(REGISTRY)
