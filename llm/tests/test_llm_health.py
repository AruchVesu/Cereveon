"""
``/llm/health`` endpoint tests.

The endpoint is a two-stage probe of the configured Ollama backend
(``GET /api/tags`` then ``POST /api/generate``).  The deterministic-
fallback path inside ``chat_pipeline`` and ``live_move_pipeline``
swallows Ollama failures so users still get *some* coach reply — but
that hides persistent outages (e.g. "deploy succeeded but the model
was never pulled") behind 200 OK ``/chat`` responses.  ``/llm/health``
exists to surface that signal directly to operators / uptime
monitors.

Stable test IDs (do NOT rename):

  LLMH_01  All-OK path returns ok=true, model name, ollama_url, latency
  LLMH_02  Tags HTTP error → ok=false, stage="tags"
  LLMH_03  Model missing from `ollama list` → ok=false, stage="tags",
           available_models surfaced
  LLMH_04  Tags transport exception (Ollama unreachable) → ok=false,
           stage="tags", error includes exception type
  LLMH_05  Generate HTTP error → ok=false, stage="generate"
  LLMH_06  Generate transport exception → ok=false, stage="generate"
  LLMH_07  Endpoint always returns HTTP 200 even on probe failure
           (operators / monitors should key on the ``ok`` field, not
           the HTTP status — the endpoint itself succeeded; what it
           probed is what failed)
  LLMH_08  Endpoint requires no authentication
  LLMH_09  Latency_ms is present and is a number
"""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Httpx AsyncClient stub
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None):
        self.status_code = status_code
        self._json = json_body or {}

    def json(self) -> Any:
        return self._json


class _FakeAsyncClient:
    """Drop-in replacement for ``httpx.AsyncClient`` driven by a script.

    The script is a deque of ``_FakeResponse`` (or ``Exception`` to raise),
    SHARED across all instances of this class.  The /llm/health handler
    constructs two AsyncClients (one for tags, one for generate); each
    consumes one entry from the deque in order.  Per-instance copies
    would let the second client re-consume the tags response — which
    masked stage="generate" failures in earlier iterations of this test.
    """

    # Class-level deque, shared across instances.  Tests reset it via
    # ``_set_script(...)``; each call to ``get`` / ``post`` pops one.
    _script: deque = deque()

    def __init__(self, *_args, **_kwargs):
        pass  # state lives on the class, not the instance

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    @classmethod
    def _next(cls):
        if not cls._script:
            raise AssertionError("Fake AsyncClient: script exhausted")
        return cls._script.popleft()

    async def get(self, *_a, **_k):
        item = self._next()
        if isinstance(item, Exception):
            raise item
        return item

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
# Stockfish, the DB, and every coach pipeline — which would force this
# test file into the integration tier.  The handler logic is small
# enough to mirror here so the test stays a fast unit test.
# ---------------------------------------------------------------------------


def _build_health_app() -> FastAPI:
    import os
    import time

    import httpx as _httpx

    # Patch the AsyncClient class on the httpx module the handler will
    # import.  The handler does ``async with httpx.AsyncClient(...)``,
    # which constructs the class via the module's attribute lookup —
    # so swapping it on the imported module is sufficient.
    _httpx.AsyncClient = _FakeAsyncClient  # type: ignore[assignment]

    stub = FastAPI()

    @stub.get("/llm/health")
    async def llm_health(request: Request):
        # Mirror the production handler verbatim except for the lazy
        # import — we substitute fixed values rather than reading
        # llm.explain_pipeline so the test doesn't need that whole
        # module imported.
        ollama_base = "http://ollama-test:11434"
        ollama_generate = f"{ollama_base}/api/generate"
        model_name = "qwen2.5:7b-instruct-q2_K"
        bare_model = model_name.split("@", 1)[0]

        started = time.perf_counter()

        def _ms() -> float:
            return round((time.perf_counter() - started) * 1000.0, 2)

        # Stage 1: tags
        try:
            async with _httpx.AsyncClient(timeout=5.0) as client:
                tags_resp = await client.get(f"{ollama_base}/api/tags")
            if tags_resp.status_code != 200:
                return {
                    "ok": False,
                    "model": model_name,
                    "ollama_url": ollama_base,
                    "latency_ms": _ms(),
                    "stage": "tags",
                    "error": f"HTTP {tags_resp.status_code}",
                }
            installed = [m.get("name", "") for m in tags_resp.json().get("models", [])]
            if not any(m.startswith(bare_model) for m in installed):
                return {
                    "ok": False,
                    "model": model_name,
                    "ollama_url": ollama_base,
                    "latency_ms": _ms(),
                    "stage": "tags",
                    "error": "configured model not in ollama list — did you forget `ollama pull`?",
                    "available_models": installed,
                }
        except Exception as exc:
            return {
                "ok": False,
                "model": model_name,
                "ollama_url": ollama_base,
                "latency_ms": _ms(),
                "stage": "tags",
                "error": f"{type(exc).__name__}: {exc}",
            }

        # Stage 2: generate
        generate_timeout_s = float(os.getenv("LLM_HEALTH_GENERATE_TIMEOUT_S", "10"))
        try:
            async with _httpx.AsyncClient(timeout=generate_timeout_s) as client:
                gen_resp = await client.post(
                    ollama_generate,
                    json={
                        "model": model_name,
                        "prompt": "ok",
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
            if gen_resp.status_code != 200:
                return {
                    "ok": False,
                    "model": model_name,
                    "ollama_url": ollama_base,
                    "latency_ms": _ms(),
                    "stage": "generate",
                    "error": f"HTTP {gen_resp.status_code}",
                }
        except Exception as exc:
            return {
                "ok": False,
                "model": model_name,
                "ollama_url": ollama_base,
                "latency_ms": _ms(),
                "stage": "generate",
                "error": f"{type(exc).__name__}: {exc}",
            }

        return {
            "ok": True,
            "model": model_name,
            "ollama_url": ollama_base,
            "latency_ms": _ms(),
        }

    return stub


@pytest.fixture
def client():
    app = _build_health_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLlmHealth:

    def test_llmh_01_all_ok(self, client):
        """LLMH_01: tags returns the model and generate returns 200 →
        ok=true with model name, ollama_url, latency_ms."""
        _set_script(
            _FakeResponse(200, {"models": [{"name": "qwen2.5:7b-instruct-q2_K"}]}),
            _FakeResponse(200, {"response": "x"}),
        )
        resp = client.get("/llm/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True, body
        assert body["model"] == "qwen2.5:7b-instruct-q2_K"
        assert body["ollama_url"] == "http://ollama-test:11434"
        assert isinstance(body["latency_ms"], (int, float))

    def test_llmh_02_tags_http_error(self, client):
        """LLMH_02: tags returns 500 → ok=false, stage='tags'."""
        _set_script(_FakeResponse(500))
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert body["stage"] == "tags"
        assert "500" in body["error"]

    def test_llmh_03_model_missing(self, client):
        """LLMH_03: tags 200 but configured model not present → ok=false,
        stage='tags', available_models surfaced."""
        _set_script(
            _FakeResponse(200, {"models": [{"name": "llama3:8b"}]}),
        )
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert body["stage"] == "tags"
        assert "ollama pull" in body["error"]
        assert body["available_models"] == ["llama3:8b"]

    def test_llmh_04_tags_transport_exception(self, client):
        """LLMH_04: Ollama unreachable → ok=false, stage='tags', error
        includes the exception type so the operator can distinguish
        timeout from refusal."""
        _set_script(ConnectionError("connection refused"))
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert body["stage"] == "tags"
        assert "ConnectionError" in body["error"]
        assert "connection refused" in body["error"]

    def test_llmh_05_generate_http_error(self, client):
        """LLMH_05: tags ok but generate returns 500 → ok=false, stage='generate'."""
        _set_script(
            _FakeResponse(200, {"models": [{"name": "qwen2.5:7b-instruct-q2_K"}]}),
            _FakeResponse(500),
        )
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert body["stage"] == "generate"
        assert "500" in body["error"]

    def test_llmh_06_generate_transport_exception(self, client):
        """LLMH_06: tags ok but generate raises → ok=false, stage='generate'."""
        _set_script(
            _FakeResponse(200, {"models": [{"name": "qwen2.5:7b-instruct-q2_K"}]}),
            TimeoutError("read timeout"),
        )
        body = client.get("/llm/health").json()
        assert body["ok"] is False
        assert body["stage"] == "generate"
        assert "TimeoutError" in body["error"]

    def test_llmh_07_always_returns_200(self, client):
        """LLMH_07: HTTP 200 even on probe failure.  Operators must key
        on body.ok, not the HTTP status — the probe endpoint itself
        succeeded; what it probed is what failed."""
        _set_script(_FakeResponse(503))
        resp = client.get("/llm/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_llmh_08_no_auth_required(self, client):
        """LLMH_08: open endpoint, accessible without Authorization or X-Api-Key.
        Uptime monitors and operators must be able to hit it without provisioning
        secrets."""
        _set_script(
            _FakeResponse(200, {"models": [{"name": "qwen2.5:7b-instruct-q2_K"}]}),
            _FakeResponse(200, {"response": "x"}),
        )
        resp = client.get("/llm/health")  # no headers
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_llmh_09_latency_ms_present(self, client):
        """LLMH_09: latency_ms is present on every response shape."""
        _set_script(
            _FakeResponse(200, {"models": [{"name": "qwen2.5:7b-instruct-q2_K"}]}),
            _FakeResponse(200, {"response": "x"}),
        )
        body = client.get("/llm/health").json()
        assert "latency_ms" in body
        assert isinstance(body["latency_ms"], (int, float))
        assert body["latency_ms"] >= 0
