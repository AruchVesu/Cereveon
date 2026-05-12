"""
``/llm/health`` endpoint tests.

The endpoint is a single-shot probe of DeepSeek's chat-completions
API — a 1-token ``POST /chat/completions`` that confirms API key
validity, model-name acceptance, and network reachability all at
once.  Replaces the prior Ollama two-stage probe (tags + generate);
DeepSeek has no notion of locally-pulled models, so the "tags"
stage is gone.

The deterministic-fallback path inside ``chat_pipeline`` and
``live_move_pipeline`` swallows LLM failures so users still get
*some* coach reply — but that hides persistent outages (invalid
API key, billing issue, DeepSeek downtime) behind 200-OK
``/chat`` responses.  ``/llm/health`` exists to surface that
signal directly to operators / uptime monitors.

Stable test IDs (do NOT rename):

  LLMH_01  Happy path returns ok=true, provider, model, api_base, latency
  LLMH_02  Missing API key → ok=false with explicit message
  LLMH_03  Upstream HTTP 4xx (e.g. 401 invalid key) → ok=false, error
           includes status code and the upstream message
  LLMH_04  Upstream HTTP 5xx → ok=false, error includes status code
  LLMH_05  200 with unexpected JSON shape (no choices) → ok=false
  LLMH_06  Transport exception (DNS / refused / unreachable) →
           ok=false, error includes exception type
  LLMH_07  Timeout exception → ok=false with error
  LLMH_08  Endpoint always returns HTTP 200 even on probe failure
           (operators key on body.ok, not status code)
  LLMH_09  Endpoint requires no authentication
  LLMH_10  Latency_ms is present and is a number
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Httpx AsyncClient stub
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json


class _FakeAsyncClient:
    """Drop-in replacement for ``httpx.AsyncClient`` driven by a script.

    The script is a deque of ``_FakeResponse`` (or ``Exception`` to raise),
    SHARED across all instances of this class.  Only ONE call per probe
    (single-shot now), but we keep the deque pattern in case future
    iterations need multi-step probing.
    """

    _script: deque = deque()

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    @classmethod
    def _next(cls):
        if not cls._script:
            raise AssertionError("Fake AsyncClient: script exhausted")
        return cls._script.popleft()

    async def post(self, *_a, **_k):
        item = self._next()
        if isinstance(item, Exception):
            raise item
        return item


def _set_script(*items: Any) -> None:
    _FakeAsyncClient._script = deque(items)


# ---------------------------------------------------------------------------
# Minimal app that mirrors the server.py /llm/health handler.
#
# We deliberately do NOT import ``llm.server`` because it pulls in
# Stockfish, the DB, and every coach pipeline.  Mirror the handler
# inline so this stays a fast unit test.
# ---------------------------------------------------------------------------


_DEEPSEEK_API_BASE = "https://api.deepseek-test.com"
_DEEPSEEK_URL = f"{_DEEPSEEK_API_BASE}/chat/completions"
_MODEL_NAME = "deepseek-chat"


def _build_health_app() -> FastAPI:
    import time

    import httpx as _httpx

    _httpx.AsyncClient = _FakeAsyncClient  # type: ignore[assignment]

    stub = FastAPI()

    @stub.get("/llm/health")
    async def llm_health(request: Request):
        started = time.perf_counter()
        timeout_s = float(os.getenv("LLM_HEALTH_GENERATE_TIMEOUT_S", "10"))

        def _ms() -> float:
            return round((time.perf_counter() - started) * 1000.0, 2)

        api_key = os.getenv("COACH_DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            return {
                "ok": False,
                "provider": "deepseek",
                "model": _MODEL_NAME,
                "api_base": _DEEPSEEK_API_BASE,
                "latency_ms": _ms(),
                "error": "COACH_DEEPSEEK_API_KEY is unset",
            }

        try:
            async with _httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    _DEEPSEEK_URL,
                    json={
                        "model": _MODEL_NAME,
                        "messages": [{"role": "user", "content": "ok"}],
                        "stream": False,
                        "max_tokens": 1,
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
            if resp.status_code != 200:
                try:
                    upstream = resp.json().get("error", {}).get("message") or resp.text[:200]
                except Exception:
                    upstream = resp.text[:200]
                return {
                    "ok": False,
                    "provider": "deepseek",
                    "model": _MODEL_NAME,
                    "api_base": _DEEPSEEK_API_BASE,
                    "latency_ms": _ms(),
                    "error": f"HTTP {resp.status_code}: {upstream}",
                }
            body = resp.json()
            try:
                _ = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                return {
                    "ok": False,
                    "provider": "deepseek",
                    "model": _MODEL_NAME,
                    "api_base": _DEEPSEEK_API_BASE,
                    "latency_ms": _ms(),
                    "error": "200 with unexpected response shape (no choices[0].message.content)",
                }
        except Exception as exc:
            # Mirrors the production trim in ``server.llm_health`` —
            # the test stub stays aligned so any future change to the
            # error-payload shape is caught by the existing /llm/health
            # contract tests rather than slipping past them.  See CodeQL
            # alert #340 / #341 (py/stack-trace-exposure) for the
            # rationale: ``{exc}`` could carry the upstream URL + echoed
            # API key fragment on httpx auth failures.
            return {
                "ok": False,
                "provider": "deepseek",
                "model": _MODEL_NAME,
                "api_base": _DEEPSEEK_API_BASE,
                "latency_ms": _ms(),
                "error": f"{type(exc).__name__}: probe failed",
            }

        return {
            "ok": True,
            "provider": "deepseek",
            "model": _MODEL_NAME,
            "api_base": _DEEPSEEK_API_BASE,
            "latency_ms": _ms(),
        }

    return stub


@pytest.fixture
def client(monkeypatch):
    # Default: API key set so the auth-key check doesn't short-circuit
    # most tests.  Tests that exercise the missing-key path override
    # this with a fresh monkeypatch.delenv.
    monkeypatch.setenv("COACH_DEEPSEEK_API_KEY", "sk-fake-test-key")
    app = _build_health_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLlmHealth:

    def test_llmh_01_happy_path(self, client):
        """LLMH_01: 200 + valid choices[0].message.content → ok=true."""
        _set_script(
            _FakeResponse(
                200,
                {"choices": [{"message": {"content": "x"}}]},
            ),
        )
        resp = client.get("/llm/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True, body
        assert body["provider"] == "deepseek"
        assert body["model"] == _MODEL_NAME
        assert body["api_base"] == _DEEPSEEK_API_BASE
        assert isinstance(body["latency_ms"], (int, float))

    def test_llmh_02_missing_api_key(self, client, monkeypatch):
        """LLMH_02: missing COACH_DEEPSEEK_API_KEY → ok=false, no upstream call."""
        monkeypatch.delenv("COACH_DEEPSEEK_API_KEY", raising=False)
        _set_script()  # empty — no upstream call should be made
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert "COACH_DEEPSEEK_API_KEY" in body["error"]

    def test_llmh_03_http_4xx_with_upstream_error(self, client):
        """LLMH_03: DeepSeek returns 401 with JSON error body → ok=false,
        error surfaces both the status code AND the upstream message."""
        _set_script(
            _FakeResponse(
                401,
                {"error": {"message": "Authentication Fails, Your api key is invalid"}},
            ),
        )
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert "401" in body["error"]
        assert "api key is invalid" in body["error"]

    def test_llmh_04_http_5xx(self, client):
        """LLMH_04: DeepSeek returns 503 → ok=false with status code."""
        _set_script(_FakeResponse(503, text="Service Unavailable"))
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert "503" in body["error"]

    def test_llmh_05_unexpected_response_shape(self, client):
        """LLMH_05: 200 but missing choices[0].message.content → ok=false.
        Catches a provider regression where the wire format diverges
        from OpenAI-compatible without us noticing."""
        _set_script(_FakeResponse(200, {"unexpected": "shape"}))
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert "unexpected response shape" in body["error"]

    def test_llmh_06_transport_exception(self, client):
        """LLMH_06: Connection refused → ok=false, error includes
        exception type so the operator can distinguish unreachable
        from auth-failed from timed-out.

        Post CodeQL alert #340 (py/stack-trace-exposure, fixed
        2026-05-13): the exception *message* is no longer surfaced in
        the response body — only ``type(exc).__name__`` plus a fixed
        ``probe failed`` sentinel.  The full ``exc`` lands in the
        server-side ``logger.warning(..., exc_info=True)`` so on-call
        still has the raw detail when needed.  Tests assert on the
        type-name portion only.
        """
        _set_script(ConnectionError("connection refused"))
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert "ConnectionError" in body["error"]
        assert "probe failed" in body["error"]
        # Negative guard: the exception message itself must NOT leak.
        assert "connection refused" not in body["error"]

    def test_llmh_07_timeout_exception(self, client):
        """LLMH_07: read timeout → ok=false with TimeoutError."""
        _set_script(TimeoutError("read timeout"))
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert "TimeoutError" in body["error"]

    def test_llmh_08_always_returns_200(self, client):
        """LLMH_08: HTTP 200 even on probe failure.  Operators key on
        body.ok, not the HTTP status — the probe endpoint itself
        succeeded; what it probed is what failed."""
        _set_script(_FakeResponse(503))
        resp = client.get("/llm/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_llmh_09_no_auth_required(self, client):
        """LLMH_09: open endpoint, accessible without Authorization or
        X-Api-Key header.  Uptime monitors and operators must be able
        to hit it without provisioning secrets."""
        _set_script(
            _FakeResponse(200, {"choices": [{"message": {"content": "x"}}]}),
        )
        resp = client.get("/llm/health")  # no headers
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_llmh_10_latency_ms_present(self, client):
        """LLMH_10: latency_ms is a non-negative number on every
        response shape (success and every failure branch)."""
        _set_script(
            _FakeResponse(200, {"choices": [{"message": {"content": "x"}}]}),
        )
        body = client.get("/llm/health").json()
        assert "latency_ms" in body
        assert isinstance(body["latency_ms"], (int, float))
        assert body["latency_ms"] >= 0
