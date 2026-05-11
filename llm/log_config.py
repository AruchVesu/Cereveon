"""Structured JSON logging for the Cereveon backend.

In production (``SECA_ENV in {prod, production}``) — and any env where
``COACH_LOG_JSON=1`` is explicitly set — every log line emitted by the
application is a single-line JSON object with a stable schema, suitable
for ingestion by Loki / Datadog / Elastic.  In dev (the default), the
stdlib ``logging`` format stays human-readable so iterative debugging
isn't a jq exercise.

Why no python-json-logger
-------------------------
Adding a dependency for a fifty-line formatter is the wrong shape.
The stdlib ``logging.Formatter`` API is enough to emit a JSON line per
LogRecord; the contextvars wiring and uvicorn-logger reconfiguration
are the actual work and live here either way.

Schema (one line per record)
----------------------------
::

    {
      "timestamp": "2026-05-11T18:30:00.123Z",
      "level": "INFO",
      "logger": "llm.server",
      "message": "request completed",
      "request_id": "abc-123",          # when set via contextvar
      "method": "GET",                  # request-end log only
      "path": "/health",
      "path_template": "/health",
      "status": 200,
      "latency_ms": 1.234,
      "client_ip": "203.0.113.10",      # if available
      "exception": {                     # only on logger.exception
        "type": "ValueError",
        "message": "...",
        "traceback": "..."
      }
    }

Any extra fields passed via ``logger.info("msg", extra={...})`` are
copied verbatim into the output object, so callers can attach
domain-specific context without modifying this module.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# Contextvars
# ---------------------------------------------------------------------------
#
# Set per-request by the middleware and read by the formatter so every log
# line emitted during a request handler carries the same request_id —
# without callers having to thread it through every signature.  Defaults
# to None so log lines outside a request scope (lifespan startup, CLI
# scripts) simply omit the field rather than emitting "null".

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def set_request_id(value: str | None) -> contextvars.Token:
    """Bind a request_id to the current async context.

    Returns the token so the caller can ``request_id_var.reset(token)``
    in a ``finally`` to undo the binding on exit (matters when one
    asyncio task spawns sub-tasks that should NOT inherit the id).
    """
    return request_id_var.set(value)


# Max length of a client-supplied X-Request-ID before we ignore it
# in favour of a fresh UUID.  Keeps a hostile client from inflating
# every log line by submitting a megabyte-long header value.
REQUEST_ID_MAX_LEN = 128


def is_valid_client_request_id(raw: str) -> bool:
    """Return True if a client-supplied X-Request-ID is safe to echo.

    Rejected:
      - empty / whitespace-only
      - longer than REQUEST_ID_MAX_LEN
      - any non-ASCII content (Loki / Datadog log pipelines are byte-
        sensitive in surprising ways; restricting to ASCII keeps the
        field safe to forward verbatim)

    The HTTP libraries used by every production client (Android's
    HttpURLConnection, OkHttp) already enforce ASCII headers at the
    wire layer; this check is defence-in-depth for the case where a
    raw socket or proxy strips that enforcement.
    """
    if not raw:
        return False
    if len(raw) > REQUEST_ID_MAX_LEN:
        return False
    if not raw.isascii():
        return False
    return True


# ---------------------------------------------------------------------------
# Reserved LogRecord attributes — anything NOT in this set, when present
# on the record, gets copied into the JSON payload as a custom field.
# This matches the python-json-logger convention: ``logger.info("msg",
# extra={"player_id": "abc"})`` → ``{"player_id": "abc", ...}``.
# ---------------------------------------------------------------------------
_RESERVED_LOG_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Emit each LogRecord as a single-line JSON object.

    The schema is documented in the module docstring.  ``json.dumps``
    with ``default=str`` so non-JSON-serialisable extras (datetime,
    UUID, etc) degrade to their ``str()`` form rather than raising
    inside the logging pipeline — a log-format crash bringing down
    the request handler is the worst kind of indirection bug.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Use record.created (seconds since epoch, UTC) — uvicorn's
        # access logger sets this consistently across platforms.
        ts = time.gmtime(record.created)
        timestamp = (
            f"{ts.tm_year:04d}-{ts.tm_mon:02d}-{ts.tm_mday:02d}T"
            f"{ts.tm_hour:02d}:{ts.tm_min:02d}:{ts.tm_sec:02d}."
            f"{int(record.msecs):03d}Z"
        )

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        # Copy any caller-attached extras (anything not in the reserved
        # LogRecord field set).  ``record.__dict__`` is the canonical
        # surface for ``extra={...}`` because the logging module merges
        # extras into the record's namespace at log-time.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_FIELDS:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            exc_type, exc_value, _exc_tb = record.exc_info
            payload["exception"] = {
                "type": exc_type.__name__ if exc_type else "Unknown",
                "message": str(exc_value) if exc_value else "",
                "traceback": self.formatException(record.exc_info),
            }

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Configuration entry point
# ---------------------------------------------------------------------------


def _should_emit_json() -> bool:
    """Decide whether this process should emit JSON logs.

    Order of precedence:
      1. ``COACH_LOG_JSON`` explicit override (1/true/yes → JSON;
         0/false/no → human-readable).  Wins regardless of env.
      2. ``SECA_ENV`` — prod / production → JSON; anything else → human.
    """
    override = os.getenv("COACH_LOG_JSON", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    if override in {"0", "false", "no"}:
        return False

    env = os.getenv("SECA_ENV", "dev").strip().lower()
    return env in {"prod", "production"}


# Module-level guard so configure_logging() is idempotent — calling it
# twice (lifespan startup + uvicorn reload, or a test re-importing the
# module) doesn't pile up duplicate handlers on the root logger.
_CONFIGURED = False


def configure_logging(force: bool = False) -> bool:
    """Install the JSON formatter on the root + uvicorn loggers.

    Returns ``True`` if JSON logging was activated, ``False`` if the
    function decided this process should keep human-readable logs (and
    therefore did nothing).  Idempotent: a second call with the same
    decision is a no-op unless ``force=True`` is set, which clears
    existing handlers and re-applies — used by tests to flip between
    modes inside one process.

    Touches three loggers:
      * root  — every ``logging.getLogger(__name__)`` inherits from this.
      * ``uvicorn.access`` — HTTP access logs.
      * ``uvicorn.error``  — uvicorn's own startup / shutdown / error log.

    Has to run AFTER uvicorn's logging setup (which uvicorn does during
    ``app.lifespan`` startup), otherwise uvicorn overwrites the
    handlers we attach.  Called from server.py lifespan, NOT module
    import time.
    """
    global _CONFIGURED  # pylint: disable=global-statement

    if _CONFIGURED and not force:
        return True

    if not _should_emit_json():
        _CONFIGURED = True
        return False

    formatter = JsonLogFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Root logger: replace all existing handlers so uvicorn's plain-text
    # default formatter doesn't double-emit.  Force=True re-runs this
    # step in tests.
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # uvicorn manages its own loggers and won't propagate to root by
    # default — patch them individually so HTTP access logs are JSON too.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        for existing in list(uv_logger.handlers):
            uv_logger.removeHandler(existing)
        uv_logger.addHandler(handler)
        uv_logger.setLevel(logging.INFO)
        # Don't propagate or root would double-emit.
        uv_logger.propagate = False

    _CONFIGURED = True
    return True
