"""Tests for llm/log_config.py — JSON structured logging + request_id flow.

Pinned invariants
-----------------
LOG_01  JsonLogFormatter emits valid JSON with the documented schema.
LOG_02  ``request_id`` from contextvar is propagated into the JSON line.
LOG_03  ``extra={...}`` fields are merged into the payload, not nested.
LOG_04  ``logger.exception`` emits an ``exception`` sub-object with type,
        message, and traceback.
LOG_05  ``_should_emit_json`` honours SECA_ENV=prod / production.
LOG_06  ``COACH_LOG_JSON=1`` forces JSON even in dev.
LOG_07  ``COACH_LOG_JSON=0`` disables JSON even in prod.
LOG_08  ``configure_logging()`` is idempotent (calling twice doesn't
        duplicate handlers).
LOG_09  Middleware sets X-Request-ID response header (echo of client
        header when provided, fresh UUID otherwise).
LOG_10  Client-supplied X-Request-ID that exceeds 128 chars is rejected
        and a fresh UUID is used instead (log-payload-size DoS guard).
LOG_11  Non-ASCII X-Request-ID is rejected.
LOG_12  Request-end log line carries method/path/path_template/status/
        latency_ms in its extras.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


# ---------------------------------------------------------------------------
# Direct unit tests on JsonLogFormatter — no FastAPI needed
# ---------------------------------------------------------------------------


def _make_record_and_format(
    *,
    msg: str = "test message",
    level: int = logging.INFO,
    extra: dict | None = None,
    exc_info: tuple | None = None,
) -> dict:
    """Build a LogRecord matching what ``logger.info(msg, extra=...)``
    would produce, run it through JsonLogFormatter, parse the JSON.
    Returns the parsed dict so individual fields can be asserted on.
    """
    from llm.log_config import JsonLogFormatter

    record = logging.LogRecord(
        name="llm.test",
        level=level,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)

    formatter = JsonLogFormatter()
    line = formatter.format(record)
    # Each line MUST be a single valid JSON object — Loki/Datadog
    # ingestion breaks on multi-line payloads.
    assert "\n" not in line, f"JSON log line contained a newline: {line!r}"
    return json.loads(line)


def test_log_01_formatter_emits_documented_schema():
    """LOG_01 — required fields are present in the JSON object."""
    payload = _make_record_and_format(msg="hello world")
    for field in ("timestamp", "level", "logger", "message"):
        assert field in payload, f"missing required field {field!r}: {payload!r}"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "llm.test"
    assert payload["message"] == "hello world"
    # Timestamp is ISO 8601 in UTC, second-precision with milliseconds.
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", payload["timestamp"]
    ), f"unexpected timestamp shape: {payload['timestamp']!r}"


def test_log_02_request_id_from_contextvar_appears_in_payload():
    """LOG_02 — request_id set via the contextvar lands in the payload."""
    from llm import log_config

    token = log_config.set_request_id("req-abc-123")
    try:
        payload = _make_record_and_format(msg="under request scope")
        assert payload.get("request_id") == "req-abc-123"
    finally:
        log_config.request_id_var.reset(token)

    # And once reset, the next record has NO request_id.
    payload = _make_record_and_format(msg="outside request scope")
    assert "request_id" not in payload


def test_log_03_extras_are_merged_into_payload():
    """LOG_03 — extras land at the top level, not in a sub-object.
    Loki/Datadog field selectors expect this shape."""
    payload = _make_record_and_format(
        msg="auth attempt",
        extra={"player_id": "p-42", "outcome": "success"},
    )
    assert payload["player_id"] == "p-42"
    assert payload["outcome"] == "success"


def test_log_04_exception_info_renders_to_sub_object():
    """LOG_04 — logger.exception emits an ``exception`` field with
    type/message/traceback."""
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    payload = _make_record_and_format(
        msg="something went wrong",
        level=logging.ERROR,
        exc_info=exc_info,
    )
    assert "exception" in payload
    exc = payload["exception"]
    assert exc["type"] == "ValueError"
    assert exc["message"] == "kaboom"
    assert "ValueError: kaboom" in exc["traceback"]


# ---------------------------------------------------------------------------
# Env-gating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_value", ["prod", "production"])
def test_log_05_should_emit_json_in_prod(monkeypatch, env_value):
    """LOG_05 — both 'prod' and 'production' (case-insensitive) → JSON."""
    monkeypatch.setenv("SECA_ENV", env_value)
    monkeypatch.delenv("COACH_LOG_JSON", raising=False)

    # Re-import to dodge module-level caching of the env value.
    import importlib

    from llm import log_config

    importlib.reload(log_config)
    assert log_config._should_emit_json() is True


def test_log_06_coach_log_json_forces_json_in_dev(monkeypatch):
    """LOG_06 — COACH_LOG_JSON=1 wins over SECA_ENV=dev."""
    monkeypatch.setenv("SECA_ENV", "dev")
    monkeypatch.setenv("COACH_LOG_JSON", "1")

    import importlib

    from llm import log_config

    importlib.reload(log_config)
    assert log_config._should_emit_json() is True


def test_log_07_coach_log_json_zero_disables_in_prod(monkeypatch):
    """LOG_07 — COACH_LOG_JSON=0 wins over SECA_ENV=prod.
    Useful for an operator who needs human-readable logs to debug
    a live incident without redeploying."""
    monkeypatch.setenv("SECA_ENV", "prod")
    monkeypatch.setenv("COACH_LOG_JSON", "0")

    import importlib

    from llm import log_config

    importlib.reload(log_config)
    assert log_config._should_emit_json() is False


def test_log_08_configure_logging_is_idempotent(monkeypatch):
    """LOG_08 — calling configure_logging twice doesn't pile up handlers."""
    monkeypatch.setenv("SECA_ENV", "prod")
    monkeypatch.setenv("COACH_LOG_JSON", "1")

    import importlib

    from llm import log_config

    importlib.reload(log_config)

    # First call sets handlers.
    assert log_config.configure_logging(force=True) is True
    root = logging.getLogger()
    handler_count_after_first = len(root.handlers)

    # Second call without force is a silent no-op.
    assert log_config.configure_logging() is True
    assert len(root.handlers) == handler_count_after_first, (
        "configure_logging() piled up duplicate handlers on a no-op second call"
    )


# ---------------------------------------------------------------------------
# Middleware integration via the live FastAPI app
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server_module():
    import llm.server as server

    return server


@pytest.fixture
def client(server_module, monkeypatch):
    import llm.seca.auth.api_key as api_key_module

    monkeypatch.setenv("SECA_API_KEY", "ci-test-key")
    monkeypatch.setenv("SECA_ENV", "dev")
    monkeypatch.setattr(api_key_module, "_API_KEY", "ci-test-key")
    monkeypatch.setattr(api_key_module, "_IS_PROD", False)
    server_module.limiter.reset()
    with TestClient(server_module.app) as c:
        yield c


def test_log_09_x_request_id_header_echoed_or_generated(client):
    """LOG_09 — server emits X-Request-ID on every response.

    When the client provides one (and it's safe), the server echoes
    it.  When the client doesn't, the server generates a fresh UUID
    so log correlation still works.
    """
    # Server-generated path.
    r1 = client.get("/health")
    assert r1.status_code == 200
    server_generated = r1.headers.get("X-Request-ID")
    assert server_generated, "/health response must carry X-Request-ID"
    # Fresh UUIDs are 32 hex chars.
    assert re.fullmatch(r"[0-9a-f]{32}", server_generated), (
        f"server-generated request id should be a uuid4 hex; got {server_generated!r}"
    )

    # Client-supplied path — server echoes it.
    supplied = "client-supplied-trace-id-7777"
    r2 = client.get("/health", headers={"X-Request-ID": supplied})
    assert r2.headers.get("X-Request-ID") == supplied


def test_log_10_oversized_client_request_id_is_rejected(client):
    """LOG_10 — client-supplied ids over 128 chars are rejected in
    favour of a fresh UUID.  Without this, a hostile client could
    submit a 1 MB X-Request-ID and inflate every log line.
    """
    huge = "x" * 1024
    r = client.get("/health", headers={"X-Request-ID": huge})
    returned = r.headers.get("X-Request-ID", "")
    assert returned != huge, "server must NOT echo a > 128-char request id"
    assert re.fullmatch(r"[0-9a-f]{32}", returned), (
        f"oversized id should be replaced with a fresh UUID; got {returned!r}"
    )


def test_log_11_non_ascii_client_request_id_is_rejected():
    """LOG_11 — non-ASCII ids are rejected.  Loki and many log
    aggregators are byte-sensitive in unexpected ways; restricting to
    ASCII keeps the field safe to forward verbatim.

    Tested at the helper level rather than through TestClient: httpx
    rejects non-ASCII headers at the wire layer before they reach the
    middleware, so an end-to-end test isn't possible.  The middleware's
    defence-in-depth is for proxies / raw-socket clients that DO let
    non-ASCII bytes through.
    """
    from llm import log_config

    assert log_config.is_valid_client_request_id("trace-id-世界-emoji") is False
    assert log_config.is_valid_client_request_id("trace-id-​-zwsp") is False
    # ASCII control characters are still ASCII per the isascii() check;
    # they're allowed here because HTTP header value parsing rejects
    # them upstream — this helper is purely about charset.
    assert log_config.is_valid_client_request_id("plain-ascii-trace") is True

    # Length boundary: exactly 128 chars is allowed; 129 is not.
    assert log_config.is_valid_client_request_id("a" * 128) is True
    assert log_config.is_valid_client_request_id("a" * 129) is False

    # Empty / whitespace is rejected.
    assert log_config.is_valid_client_request_id("") is False


def test_log_12_request_end_log_carries_full_extras(client, server_module):
    """LOG_12 — the request-end INFO line at the bottom of the
    prometheus middleware must carry method/path/path_template/status/
    latency_ms in its ``extra``.  Capture via a logging.Handler that
    saves the records, then inspect.
    """
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.INFO)
    server_logger = logging.getLogger("llm.server")
    server_logger.addHandler(handler)
    try:
        # Some unique path so only this request's log line is in scope.
        r = client.get("/health")
        assert r.status_code == 200
    finally:
        server_logger.removeHandler(handler)

    request_end = [r for r in captured if r.getMessage() == "request completed"]
    assert request_end, (
        f"no request-completed log emitted; captured messages: "
        f"{[r.getMessage() for r in captured]!r}"
    )
    rec = request_end[-1]
    assert getattr(rec, "method", None) == "GET"
    assert getattr(rec, "path", None) == "/health"
    assert getattr(rec, "path_template", None) == "/health"
    assert getattr(rec, "status", None) == 200
    latency = getattr(rec, "latency_ms", None)
    assert isinstance(latency, (int, float)) and latency >= 0.0
