"""
API schema-versioning middleware tests — llm/tests/test_api_version_header.py

Pin the contract that the ``X-API-Version`` header advertised by the
Android client and emitted by the server backs a real version-mismatch
gate at the HTTP boundary.

Server contract (Phase 1 — lenient on missing, strict on mismatch):

  * Every response carries ``X-API-Version`` matching the server's
    ``API_VERSION`` constant.  Discovery routes (``/``, ``/health``,
    ``/seca/status``) emit it too — so an out-of-date client can read
    the header off /seca/status and surface a clear "update the app"
    UI without ever hitting a coaching endpoint.
  * On coaching endpoints, an incoming ``X-API-Version`` header is
    validated:
      - Missing  → request proceeds (Phase 1 lenient mode); an INFO
        log records the missing-header request so the operator can
        watch the rollout migrate to fully-versioned clients.
      - Equal to ``API_VERSION``  → request proceeds silently.
      - Anything else            → HTTP 400 with a JSON
        ``{"detail": "..."}`` body that names both versions.
  * CORS preflights allow the ``X-API-Version`` request header so
    browsers / WebView clients aren't blocked at the preflight.

Stable test IDs (do NOT rename):
  AVH_01  Constant value pin (API_VERSION == "1")
  AVH_02  Health response carries X-API-Version
  AVH_03  /seca/status response carries X-API-Version
  AVH_04  Coaching response carries X-API-Version
  AVH_05  Missing header → 200 (lenient on coaching path)
  AVH_06  Matching header → 200
  AVH_07  Mismatched header → 400 with clear detail
  AVH_08  Mismatch response still carries server version header
          (so the client can update its constant on receipt)
  AVH_09  Discovery routes are exempt from mismatch rejection
  AVH_10  CORS allow_headers includes X-API-Version
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")


@pytest.fixture(scope="module")
def server_module():
    import llm.server as server

    return server


@pytest.fixture
def client(server_module, monkeypatch):
    """Per-test client with two cross-test contamination paths neutered.

    The full CI suite flakes this module's tests in aggregate without
    these fixtures:

      1.  ``llm.seca.auth.api_key`` reads ``os.getenv("SECA_API_KEY")``
          *at module import time* into a private ``_API_KEY`` constant.
          Other tests that pop the env var (and test the missing-key
          path) don't reset that constant.  ``monkeypatch.setenv`` here
          is insufficient because the module's already-imported cache
          is what the request handler reads — we patch the constant
          directly.
      2.  Other tests hit ``/analyze`` enough to exhaust the slowapi
          30/min bucket.  ``server_module.limiter.reset()`` clears
          the in-memory state.

    Both fixtures are function-scoped so the resets land before every
    test in this module, not just the first.  ``monkeypatch`` auto-
    undoes both the env and the attribute override on teardown.
    """
    import llm.seca.auth.api_key as api_key_module

    monkeypatch.setenv("SECA_API_KEY", "ci-test-key")
    monkeypatch.setenv("SECA_ENV", "dev")
    monkeypatch.setattr(api_key_module, "_API_KEY", "ci-test-key")
    monkeypatch.setattr(api_key_module, "_IS_PROD", False)
    server_module.limiter.reset()
    with TestClient(server_module.app) as c:
        yield c


def test_avh_01_api_version_constant(server_module) -> None:
    assert server_module.API_VERSION == "1", (
        "API_VERSION constant must equal '1' until a deliberate bump.  "
        "Bumping requires updating both server and Android constants in "
        "the same release; see docs/API_CONTRACTS.md > API schema versioning."
    )


def test_avh_02_health_response_carries_version(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-API-Version") == "1"


def test_avh_03_seca_status_response_carries_version(client) -> None:
    """AVH_03: /seca/status MUST carry the version header so an
    out-of-date client can discover the server version off the same
    open endpoint it already polls for the safety gate."""
    resp = client.get("/seca/status")
    assert resp.status_code == 200
    assert resp.headers.get("X-API-Version") == "1"


def test_avh_04_coaching_response_carries_version(client) -> None:
    """AVH_04: a coaching request that succeeds carries the version
    header on the way back too."""
    # /analyze accepts an API-key + a FEN-only body and returns the
    # engine signal — no JWT needed, deterministic, fast.  ``stockfish_json``
    # was removed in PR 9 (trust-boundary fix; the ESV builder no
    # longer accepts client-supplied JSON), so the request body is
    # just ``{"fen": ...}``.
    resp = client.post(
        "/analyze",
        headers={"X-Api-Key": "ci-test-key"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text!r}"
    assert resp.headers.get("X-API-Version") == "1"


def test_avh_05_missing_header_is_lenient(client) -> None:
    """AVH_05: Phase 1 lenient mode — a coaching call with NO
    X-API-Version header proceeds normally.  This preserves backward
    compatibility with old in-the-field clients while the new clients
    roll out."""
    resp = client.post(
        "/analyze",
        headers={"X-Api-Key": "ci-test-key"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-API-Version") == "1"


def test_avh_06_matching_header_is_accepted(client) -> None:
    resp = client.post(
        "/analyze",
        headers={"X-Api-Key": "ci-test-key", "X-API-Version": "1"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 200


def test_avh_07_mismatched_header_is_rejected(client) -> None:
    """AVH_07: an explicit version mismatch returns 400 — strict mode
    on mismatch.  The detail message must name both versions so the
    operator sees what's wrong without grepping the source."""
    resp = client.post(
        "/analyze",
        headers={"X-Api-Key": "ci-test-key", "X-API-Version": "999"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", "")
    assert "999" in detail and "1" in detail, (
        f"detail must name both client and server versions; got {detail!r}"
    )


def test_avh_08_mismatch_response_carries_server_version(client) -> None:
    """AVH_08: even on rejection, the response carries the server's
    X-API-Version header so the client can update its build constant
    on the next release without needing to re-query a discovery
    endpoint."""
    resp = client.post(
        "/analyze",
        headers={"X-Api-Key": "ci-test-key", "X-API-Version": "999"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 400
    assert resp.headers.get("X-API-Version") == "1"


@pytest.mark.parametrize("path", ["/", "/health", "/seca/status"])
def test_avh_09_discovery_routes_exempt_from_mismatch(client, path: str) -> None:
    """AVH_09: discovery routes (the surface a client uses to find out
    *what* version the server speaks) NEVER reject on mismatch.
    Otherwise an out-of-date client couldn't even read /seca/status to
    learn that it's out of date."""
    resp = client.get(path, headers={"X-API-Version": "999"})
    assert resp.status_code == 200, (
        f"discovery route {path} must accept any X-API-Version; "
        f"got {resp.status_code}"
    )
    assert resp.headers.get("X-API-Version") == "1"


def test_avh_10_cors_allow_headers_includes_x_api_version(server_module) -> None:
    """AVH_10: the CORS allow_headers list must include X-API-Version
    so browser-based clients don't fail the preflight on the new
    custom header.  Inspects the CORSMiddleware configuration on the
    live FastAPI app."""
    app = server_module.app
    cors_middleware = None
    for mw in app.user_middleware:
        # Starlette stores the middleware class either in `cls` (older)
        # or callable in `kwargs` — check both for compat.
        if "CORSMiddleware" in repr(mw):
            cors_middleware = mw
            break
    assert cors_middleware is not None, "CORSMiddleware not found on app.user_middleware"
    # The middleware's options live in its kwargs.
    options = cors_middleware.kwargs if hasattr(cors_middleware, "kwargs") else {}
    allow_headers = options.get("allow_headers", [])
    assert "X-API-Version" in allow_headers, (
        f"CORS allow_headers must include 'X-API-Version'; got {allow_headers!r}"
    )
