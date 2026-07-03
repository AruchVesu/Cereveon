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
* LLM calls       -> ``observe_llm_call`` invoked by
                     ``llm.seca.coach.explain_pipeline.call_llm`` on every
                     DeepSeek round-trip (success and failure).
* Hardware        -> CPU / memory / disk / load-avg gauges sampled by
                     callback at Prometheus scrape time via ``psutil``.

Exposition
----------
``get_metrics_text()`` returns the wire-format bytes; the /metrics
route in server.py wraps it with ``Response(content_type=METRICS_CONTENT_TYPE)``.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

import psutil
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
# server_error — plus, for POST /auth/lichess (which shares this counter):
# lichess_success, lichess_oauth_failed, lichess_rate_limited,
# lichess_upstream_error.  ``register`` and ``login`` are separate counters
# since their failure modes diverge (register can fail on duplicate email,
# login can't).
auth_login_total = Counter(
    "chesscoach_auth_login_total",
    "Login attempts on /auth/login and /auth/lichess, labeled by outcome.",
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
# LLM call metrics (DeepSeek round-trip surface)
# ---------------------------------------------------------------------------
#
# Recorded by ``llm.seca.coach.explain_pipeline.call_llm`` on every LLM
# round-trip — success, timeout, HTTP error, or empty body.  The
# ``outcome`` label is the same vocabulary on the histogram and the
# error counter so a Grafana panel can correlate latency tail with
# specific failure modes.

# Wall-clock for the whole ``httpx.stream`` block, including SSE
# consumption.  Buckets tuned for DeepSeek's typical 2 - 30 s window
# (chat completions; deepseek-reasoner can push 60 s+).
llm_request_duration_seconds = Histogram(
    "chesscoach_llm_request_duration_seconds",
    "Wall-clock duration of a DeepSeek call (full SSE consumption), in seconds.",
    labelnames=("model", "outcome"),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0),
)

# Token usage as reported by the provider's ``usage`` block.  ``kind``
# is ``prompt`` or ``completion`` — keeping them on separate series
# means a Grafana panel can plot both without arithmetic and the
# cost-derivation queries below can use ``sum by (kind)``.
llm_tokens_total = Counter(
    "chesscoach_llm_tokens_total",
    "DeepSeek tokens used, summed across all calls.",
    labelnames=("model", "kind"),
)

# Cost is derived from ``llm_tokens_total`` and the ``_PRICE_PER_1K``
# table below.  Stored as its own counter (rather than a recording rule
# in Grafana) so the dashboard works against any Prometheus that has
# the metric — no recording-rule plumbing required.
llm_cost_usd_total = Counter(
    "chesscoach_llm_cost_usd_total",
    "Estimated USD cost of DeepSeek calls, derived from prompt+completion tokens.",
    labelnames=("model", "kind"),
)

# Failure counter — incremented on every non-``ok`` outcome.  Split from
# the histogram so a single rate() over a 5 m window gives the error
# rate directly without histogram-bucket arithmetic.
llm_errors_total = Counter(
    "chesscoach_llm_errors_total",
    "DeepSeek call failures, labeled by classified category.",
    labelnames=("model", "category"),
)


# DeepSeek pricing, verified against the official page 2026-07-03.
# Tuple is (prompt_per_1k, completion_per_1k) in USD, billed at the
# cache-MISS input rate (the cache-hit discount is not modelled, so the
# cost metric slightly OVERSTATES true spend — acceptable: conservative).
# Source: https://api-docs.deepseek.com/quick_start/pricing
#
# revisit: 2026-07-15 — TWO dated events, both before the old 2026-08
# note would have fired:
#   1. Mid-July 2026: DeepSeek has announced a price increase (~2x per
#      token).  Update BOTH rows when it lands; the freemium margins
#      were checked at 2x and hold (see the unit-economics memory /
#      PaywallActivityTest launch-pricing rationale).
#   2. 2026-07-24: the model NAMES deepseek-chat / deepseek-reasoner are
#      deprecated in favour of deepseek-v4-flash (non-thinking /
#      thinking).  The v4-flash row below exists so bumping
#      COACH_DEEPSEEK_MODEL does not silently zero the cost metric
#      (_cost_for returns 0.0 for unknown model names).
_PRICE_PER_1K: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.00014, 0.00028),
    # Same engine as deepseek-chat post-V4 (the legacy name is an alias
    # for v4-flash non-thinking); priced identically today.
    "deepseek-v4-flash": (0.00014, 0.00028),
    # deepseek-reasoner is ~4x; price-listed as cache-miss input + reasoning output.
    "deepseek-reasoner": (0.00055, 0.00219),
}


def _cost_for(model: str, kind: str, tokens: int) -> float:
    """Return USD cost for ``tokens`` of ``kind`` (prompt|completion) on ``model``.

    Unknown models return 0.0 with a one-time warning.  Cost is an
    observability signal, never a control signal — a missing price entry
    must never raise inside the LLM hot path.
    """
    prices = _PRICE_PER_1K.get(model)
    if prices is None:
        if model not in _COST_WARNED_MODELS:
            logger.warning("no DeepSeek price entry for model %r; cost metric will read 0", model)
            _COST_WARNED_MODELS.add(model)
        return 0.0
    prompt_price, completion_price = prices
    rate = prompt_price if kind == "prompt" else completion_price
    return (tokens / 1000.0) * rate


_COST_WARNED_MODELS: set[str] = set()


def cost_for_call(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return total USD cost for one call across prompt + completion tokens."""
    return _cost_for(model, "prompt", prompt_tokens) + _cost_for(
        model, "completion", completion_tokens
    )


def observe_llm_call(
    *,
    model: str,
    outcome: str,
    duration_seconds: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error_category: str | None = None,
) -> None:
    """Record one LLM round-trip on every Prometheus surface at once.

    Called from ``call_llm`` in ``llm.seca.coach.explain_pipeline``.  Keeps
    the metric vocabulary (``outcome``, ``category``) defined in one
    place so callers don't drift on label values.

    ``outcome`` is one of: ``ok`` / ``timeout`` / ``http_error`` /
    ``parse_error`` / ``empty`` / ``config_error``.  When ``outcome``
    is not ``ok``, supply ``error_category`` so ``llm_errors_total``
    has a non-empty value — defaults to ``outcome`` if omitted.

    Tokens are recorded only on ``ok`` (when ``usage`` is available).
    Cost is derived inline from the same values so the two counters
    stay in lock-step.
    """
    llm_request_duration_seconds.labels(model=model, outcome=outcome).observe(
        max(0.0, duration_seconds)
    )

    if outcome == "ok":
        if prompt_tokens > 0:
            llm_tokens_total.labels(model=model, kind="prompt").inc(prompt_tokens)
            llm_cost_usd_total.labels(model=model, kind="prompt").inc(
                _cost_for(model, "prompt", prompt_tokens)
            )
        if completion_tokens > 0:
            llm_tokens_total.labels(model=model, kind="completion").inc(completion_tokens)
            llm_cost_usd_total.labels(model=model, kind="completion").inc(
                _cost_for(model, "completion", completion_tokens)
            )
        return

    category = error_category or outcome
    llm_errors_total.labels(model=model, category=category).inc()


# ---------------------------------------------------------------------------
# Hardware metrics (CPU / memory / disk / load-avg)
# ---------------------------------------------------------------------------
#
# Callback-based gauges so the cost is paid only when Alloy scrapes
# (default 30 s in ``monitoring/alloy.alloy``).  Identical pattern to
# the engine-pool gauges above so the mental model is consistent.
#
# ``psutil.cpu_percent(interval=None)`` is non-blocking: it returns the
# delta since the previous call.  The first call after process start
# returns 0.0 by design — at a 30 s scrape cadence the second scrape
# carries a real number, so this is acceptable and intentional.
#
# Disk is sampled at ``/`` only on Linux (production) — Windows dev
# machines fall back to the current working directory's drive root so
# the gauge stays populated without a per-OS conditional at the call
# site.

_HARDWARE_DISK_PATH = os.getenv(
    "CHESSCOACH_DISK_METRIC_PATH",
    "/" if os.name == "posix" else os.path.splitdrive(os.getcwd())[0] or ".",
)


def _safe_cpu_percent() -> float:
    try:
        return float(psutil.cpu_percent(interval=None))
    except Exception:  # pylint: disable=broad-except
        logger.exception("psutil.cpu_percent failed; returning 0")
        return 0.0


def _safe_memory_percent() -> float:
    try:
        return float(psutil.virtual_memory().percent)
    except Exception:  # pylint: disable=broad-except
        logger.exception("psutil.virtual_memory failed; returning 0")
        return 0.0


def _safe_memory_used_bytes() -> float:
    try:
        return float(psutil.virtual_memory().used)
    except Exception:  # pylint: disable=broad-except
        logger.exception("psutil.virtual_memory failed; returning 0")
        return 0.0


def _safe_disk_percent() -> float:
    try:
        return float(psutil.disk_usage(_HARDWARE_DISK_PATH).percent)
    except Exception:  # pylint: disable=broad-except
        logger.exception("psutil.disk_usage(%s) failed; returning 0", _HARDWARE_DISK_PATH)
        return 0.0


def _safe_load_avg_1m() -> float:
    # getloadavg is unix-only.  On Windows dev machines, psutil emulates
    # it via a background thread in some versions; fall back to 0 if the
    # call isn't available rather than letting the scrape fail.
    if not hasattr(psutil, "getloadavg"):
        return 0.0
    try:
        load1, _, _ = psutil.getloadavg()
        return float(load1)
    except (OSError, AttributeError):
        return 0.0


cpu_percent = Gauge(
    "chesscoach_cpu_percent",
    "Process-host CPU utilisation percentage (0-100), per psutil.cpu_percent.",
)
cpu_percent.set_function(_safe_cpu_percent)

memory_percent = Gauge(
    "chesscoach_memory_percent",
    "Process-host memory utilisation percentage (0-100), per psutil.virtual_memory.",
)
memory_percent.set_function(_safe_memory_percent)

memory_used_bytes = Gauge(
    "chesscoach_memory_used_bytes",
    "Process-host memory used in bytes, per psutil.virtual_memory().used.",
)
memory_used_bytes.set_function(_safe_memory_used_bytes)

disk_percent = Gauge(
    "chesscoach_disk_percent",
    "Disk utilisation percentage (0-100) of the configured mountpoint.",
)
disk_percent.set_function(_safe_disk_percent)

load_avg_1m = Gauge(
    "chesscoach_load_avg_1m",
    "Unix 1-minute load average (0.0 on platforms without getloadavg).",
)
load_avg_1m.set_function(_safe_load_avg_1m)


# ---------------------------------------------------------------------------
# Exposition
# ---------------------------------------------------------------------------


def get_metrics_text() -> bytes:
    """Return the Prometheus wire-format payload for the default registry."""
    return generate_latest(REGISTRY)
