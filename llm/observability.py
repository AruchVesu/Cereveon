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

# Prompt-cache breakdown of the ``kind="prompt"`` series above.  DeepSeek's
# automatic context cache bills cache-HIT input tokens at a fraction of the
# miss rate (50x cheaper on v4-flash as of 2026-07-10), and reports the
# split per call as ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
# in the usage block.  A SEPARATE counter (rather than new ``kind`` values on
# ``llm_tokens_total``) keeps existing ``sum by (kind)`` dashboard queries
# from double-counting prompt tokens.  ``result`` is ``hit`` or ``miss``;
# hit_rate = hit / (hit + miss).  This is the cache-efficiency signal the
# prompt-prefix layout work (static system prompt + app guide first,
# append-only history before per-position content) is measured by.
llm_prompt_cache_tokens_total = Counter(
    "chesscoach_llm_prompt_cache_tokens_total",
    "DeepSeek prompt tokens split by context-cache result (hit|miss).",
    labelnames=("model", "result"),
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


# DeepSeek pricing, verified against the official page 2026-07-10.
# Tuple is (prompt_miss_per_1k, prompt_cache_hit_per_1k, completion_per_1k)
# in USD.  The context cache is automatic (no request opt-in): repeated
# request PREFIXES bill at the cache-HIT rate, everything else at the
# cache-MISS rate.  When a call's hit/miss split is unknown (older
# gateways, estimated usage on aborted streams), the full prompt is
# billed at the MISS rate — the cost metric then slightly OVERSTATES
# true spend, which is the conservative direction.
# Source: https://api-docs.deepseek.com/quick_start/pricing
#
# revisit: 2026-07-15 — TWO dated events, both before the old 2026-08
# note would have fired:
#   1. Mid-July 2026: DeepSeek has announced a price increase (~2x per
#      token).  Update ALL columns when it lands; the freemium margins
#      were checked at 2x and hold (see the unit-economics memory /
#      PaywallActivityTest launch-pricing rationale).
#   2. 2026-07-24: the model NAMES deepseek-chat / deepseek-reasoner are
#      deprecated in favour of deepseek-v4-flash (non-thinking /
#      thinking).  The v4-flash row below exists so bumping
#      COACH_DEEPSEEK_MODEL does not silently zero the cost metric
#      (_cost_for returns 0.0 for unknown model names).
_PRICE_PER_1K: dict[str, tuple[float, float, float]] = {
    "deepseek-chat": (0.00014, 0.0000028, 0.00028),
    # Same engine as deepseek-chat post-V4 (the legacy name is an alias
    # for v4-flash non-thinking); priced identically today.
    "deepseek-v4-flash": (0.00014, 0.0000028, 0.00028),
    # deepseek-reasoner (v4-flash thinking): miss + completion rates
    # verified 2026-07-03.  Its cache-hit rate is carried at the last
    # pre-V4 published value ($0.14/M) rather than the v4-flash hit rate
    # — deliberately conservative for a model production never uses.
    "deepseek-reasoner": (0.00055, 0.00014, 0.00219),
}


def _cost_for(model: str, kind: str, tokens: int) -> float:
    """Return USD cost for ``tokens`` of ``kind`` on ``model``.

    ``kind`` is one of ``prompt`` / ``prompt_cache_miss`` (both billed at
    the cache-miss input rate — bare ``prompt`` is the conservative
    no-split-known path), ``prompt_cache_hit``, or ``completion``.

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
    miss_price, hit_price, completion_price = prices
    if kind == "prompt_cache_hit":
        rate = hit_price
    elif kind == "completion":
        rate = completion_price
    else:  # "prompt" or "prompt_cache_miss"
        rate = miss_price
    return (tokens / 1000.0) * rate


_COST_WARNED_MODELS: set[str] = set()


def _prompt_cost(
    model: str, prompt_tokens: int, cache_hit_tokens: int, cache_miss_tokens: int
) -> float:
    """USD cost of the prompt side of one call, cache-split-aware.

    Uses the hit/miss split only when it exactly accounts for
    ``prompt_tokens`` (DeepSeek guarantees hit + miss == prompt_tokens);
    any other shape falls back to billing the whole prompt at the miss
    rate — conservative, and identical to the pre-split behaviour.
    """
    if (
        prompt_tokens > 0
        and cache_hit_tokens >= 0
        and cache_miss_tokens >= 0
        and cache_hit_tokens + cache_miss_tokens == prompt_tokens
        and cache_hit_tokens + cache_miss_tokens > 0
    ):
        return _cost_for(model, "prompt_cache_hit", cache_hit_tokens) + _cost_for(
            model, "prompt_cache_miss", cache_miss_tokens
        )
    return _cost_for(model, "prompt", prompt_tokens)


def cost_for_call(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> float:
    """Return total USD cost for one call across prompt + completion tokens.

    When the caller supplies the provider-reported prompt-cache split,
    the prompt side is priced hit/miss-aware; otherwise (both zero, or a
    split that doesn't add up) the whole prompt bills at the miss rate,
    matching the pre-2026-07 behaviour exactly.
    """
    return _prompt_cost(model, prompt_tokens, cache_hit_tokens, cache_miss_tokens) + _cost_for(
        model, "completion", completion_tokens
    )


def observe_llm_call(
    *,
    model: str,
    outcome: str,
    duration_seconds: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    error_category: str | None = None,
) -> None:
    """Record one LLM round-trip on every Prometheus surface at once.

    Called from ``call_llm`` / ``call_llm_stream`` in
    ``llm.seca.coach.explain_pipeline``.  Keeps the metric vocabulary
    (``outcome``, ``category``) defined in one place so callers don't
    drift on label values.

    ``outcome`` is one of: ``ok`` / ``aborted`` / ``timeout`` /
    ``http_error`` / ``parse_error`` / ``empty`` / ``config_error``.
    When ``outcome`` is not ``ok``, supply ``error_category`` so
    ``llm_errors_total`` has a non-empty value — defaults to ``outcome``
    if omitted.

    Tokens (and their derived cost) are recorded on ``ok`` AND on
    ``aborted`` — an aborted stream is a healthy generation the consumer
    abandoned mid-flight (validator rejection in the streaming pipeline),
    so its tokens were genuinely billed; the caller passes an ESTIMATE
    when the provider's usage frame never arrived.  Transport-failure
    outcomes (timeout / http_error / …) keep ignoring token args: no
    usage exists for them.  ``aborted`` additionally increments
    ``llm_errors_total`` (category ``aborted``) because the attempt did
    not produce a served reply.

    ``cache_hit_tokens`` / ``cache_miss_tokens`` are the provider-reported
    prompt-cache split; when they exactly account for ``prompt_tokens``
    the prompt cost is priced hit/miss-aware and the split lands on
    ``llm_prompt_cache_tokens_total``.  Zeros (split unknown) preserve
    the flat cache-miss pricing.
    """
    llm_request_duration_seconds.labels(model=model, outcome=outcome).observe(
        max(0.0, duration_seconds)
    )

    if outcome in ("ok", "aborted"):
        if prompt_tokens > 0:
            llm_tokens_total.labels(model=model, kind="prompt").inc(prompt_tokens)
            llm_cost_usd_total.labels(model=model, kind="prompt").inc(
                _prompt_cost(model, prompt_tokens, cache_hit_tokens, cache_miss_tokens)
            )
            if (
                cache_hit_tokens >= 0
                and cache_miss_tokens >= 0
                and cache_hit_tokens + cache_miss_tokens == prompt_tokens
            ):
                if cache_hit_tokens > 0:
                    llm_prompt_cache_tokens_total.labels(model=model, result="hit").inc(
                        cache_hit_tokens
                    )
                if cache_miss_tokens > 0:
                    llm_prompt_cache_tokens_total.labels(model=model, result="miss").inc(
                        cache_miss_tokens
                    )
        if completion_tokens > 0:
            llm_tokens_total.labels(model=model, kind="completion").inc(completion_tokens)
            llm_cost_usd_total.labels(model=model, kind="completion").inc(
                _cost_for(model, "completion", completion_tokens)
            )
        if outcome == "ok":
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
