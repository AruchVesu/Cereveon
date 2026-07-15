"""
API schema-versioning middleware tests — llm/tests/test_api_version_header.py

Pin the contract that the ``X-API-Version`` header advertised by the
Android client and emitted by the server backs a real version-mismatch
gate at the HTTP boundary.

Server contract (Phase 2 — lenient on missing, strict on unsupported,
                  PR 14 added supported-range advertisement):

  * Every response carries ``X-API-Version`` (server's current /
    preferred version) AND ``X-API-Versions-Supported`` (comma-
    separated list of every version the server accepts).  Discovery
    routes (``/``, ``/health``, ``/seca/status``) emit both — so an
    out-of-date client can read the headers off /seca/status and
    surface a clear "update the app" UI without ever hitting a
    coaching endpoint.
  * On coaching endpoints, an incoming ``X-API-Version`` header is
    validated:
      - Missing                       → request proceeds (lenient mode);
        an INFO log records the missing-header request so the
        operator can watch the rollout migrate to fully-versioned
        clients.
      - In ``API_VERSIONS_SUPPORTED`` → request proceeds silently.
      - Anything else                 → HTTP 400 with a JSON
        ``{"detail": "..."}`` body that names the supported range.
  * CORS preflights allow the ``X-API-Version`` request header so
    browsers / WebView clients aren't blocked at the preflight.
  * CORS expose_headers includes both response headers so browser
    scripts can read them (non-browser clients are unaffected).

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
  AVH_11  Response carries X-API-Versions-Supported (added PR 14)
  AVH_12  X-API-Versions-Supported value matches the tuple joined by
          ", " (added PR 14)
  AVH_13  API_VERSION equals API_VERSIONS_SUPPORTED[-1] (added PR 14)
  AVH_14  Error detail names the supported range (added PR 14)
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
    assert server_module.API_VERSION == "2", (
        "API_VERSION constant must equal '2' until the next deliberate bump.  "
        "Bumped 1 -> 2 in the Lichess v2 async-import PR (POST /lichess/import "
        "switches response shape from 200 + summary dict to 202 + job payload "
        "when the client sends X-API-Version: 2).  '1' remains in "
        "API_VERSIONS_SUPPORTED for backward compat with shipped Android "
        "builds — see test_avh_06 + the next bump checklist in README."
    )


def test_avh_02_health_response_carries_version(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-API-Version") == "2"


def test_avh_03_seca_status_response_carries_version(client) -> None:
    """AVH_03: /seca/status MUST carry the version header so an
    out-of-date client can discover the server version off the same
    open endpoint it already polls for the safety gate."""
    resp = client.get("/seca/status")
    assert resp.status_code == 200
    assert resp.headers.get("X-API-Version") == "2"


def test_avh_04_coaching_response_carries_version(client) -> None:
    """AVH_04: a coaching request that succeeds carries the version
    header on the way back too."""
    # /engine/eval accepts an API-key + a FEN-only body and returns the
    # engine eval (score + best_move).  No JWT needed, deterministic,
    # fast.  Originally this test used /analyze; that route was retired
    # in PR 22 (no Android caller), so the X-API-Version coverage moved
    # to /engine/eval which has the same auth shape.
    resp = client.post(
        "/engine/eval",
        headers={"X-Api-Key": "ci-test-key"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text!r}"
    assert resp.headers.get("X-API-Version") == "2"


def test_avh_05_missing_header_is_lenient(client) -> None:
    """AVH_05: Phase 1 lenient mode — a coaching call with NO
    X-API-Version header proceeds normally.  This preserves backward
    compatibility with old in-the-field clients while the new clients
    roll out."""
    resp = client.post(
        "/engine/eval",
        headers={"X-Api-Key": "ci-test-key"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-API-Version") == "2"


def test_avh_06_matching_header_is_accepted(client) -> None:
    resp = client.post(
        "/engine/eval",
        headers={"X-Api-Key": "ci-test-key", "X-API-Version": "1"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 200


def test_avh_07_mismatched_header_is_rejected(client) -> None:
    """AVH_07: an explicit version mismatch returns 400 — strict mode
    on mismatch.  The detail message must name both versions so the
    operator sees what's wrong without grepping the source."""
    resp = client.post(
        "/engine/eval",
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
        "/engine/eval",
        headers={"X-Api-Key": "ci-test-key", "X-API-Version": "999"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 400
    assert resp.headers.get("X-API-Version") == "2"


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
    assert resp.headers.get("X-API-Version") == "2"


def test_cors_order_01_cors_is_the_outermost_middleware(server_module) -> None:
    """CORS_ORDER_01 (audit 2026-07-14, P2 #10): CORSMiddleware must be
    the LAST-added — therefore OUTERMOST — middleware.  Starlette wraps
    inside-out (``add_middleware`` inserts at ``user_middleware[0]``),
    and only an outermost CORS layer can stamp
    Access-Control-Allow-Origin on responses short-circuited by the
    other middleware (_LimitBodySize 413/411, api_version_gate 400, the
    security-header wrapper's error paths).  Latent today (no browser
    client ships) but silently wrong to leave inverted."""
    outermost = server_module.app.user_middleware[0]
    assert "CORSMiddleware" in repr(outermost), (
        "CORSMiddleware must be registered LAST (outermost); "
        f"found {outermost!r} in the outermost slot instead"
    )


def test_cors_order_02_oversized_body_413_carries_cors_headers(client) -> None:
    """CORS_ORDER_02: a response produced by the body-size middleware
    (outer layer before this fix) must now carry the CORS header for an
    allowed origin — the behavioural consequence of CORS_ORDER_01."""
    resp = client.post(
        "/engine/eval",
        headers={
            "X-Api-Key": "ci-test-key",
            "Origin": "http://localhost:5173",
            "Content-Type": "application/json",
        },
        content=b"x" * (512 * 1024 + 1),
    )
    assert resp.status_code == 413
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173", (
        "413 from _LimitBodySize must carry Access-Control-Allow-Origin "
        "now that CORS wraps outermost"
    )


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


# ---------------------------------------------------------------------------
# PR 14 — supported-range advertisement (Phase 2 negotiation)
# ---------------------------------------------------------------------------


def test_avh_11_response_carries_supported_versions_header(client) -> None:
    """AVH_11: every response carries ``X-API-Versions-Supported``.

    Pinned across both the coaching path and a discovery route so a
    future revision that adds the header to only one path trips this
    test.
    """
    coaching = client.post(
        "/analyze",
        headers={"X-Api-Key": "ci-test-key"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert coaching.headers.get("X-API-Versions-Supported"), (
        "coaching response missing X-API-Versions-Supported header"
    )

    discovery = client.get("/health")
    assert discovery.headers.get("X-API-Versions-Supported"), (
        "discovery response missing X-API-Versions-Supported header"
    )


def test_avh_12_supported_header_matches_tuple(server_module, client) -> None:
    """AVH_12: ``X-API-Versions-Supported`` reflects
    ``API_VERSIONS_SUPPORTED`` joined by ', '.

    Direct value-match so a future revision that bumps the tuple but
    forgets to refresh the cached header value
    (``_API_VERSIONS_SUPPORTED_HEADER``) trips this test.
    """
    expected = ", ".join(server_module.API_VERSIONS_SUPPORTED)
    resp = client.get("/health")
    assert resp.headers.get("X-API-Versions-Supported") == expected, (
        f"X-API-Versions-Supported header must equal {expected!r}; "
        f"got {resp.headers.get('X-API-Versions-Supported')!r}"
    )


def test_avh_13_current_version_is_last_in_supported(server_module) -> None:
    """AVH_13: ``API_VERSION`` must always equal
    ``API_VERSIONS_SUPPORTED[-1]``.

    The "current/preferred" version is conventionally the most-recent
    entry in the supported tuple.  A future revision that bumps
    ``API_VERSION`` without appending to the tuple (or vice versa)
    breaks the invariant that drives the rollout flow described in
    README > API schema versioning.
    """
    assert server_module.API_VERSION == server_module.API_VERSIONS_SUPPORTED[-1], (
        f"API_VERSION ({server_module.API_VERSION!r}) must equal "
        f"API_VERSIONS_SUPPORTED[-1] "
        f"({server_module.API_VERSIONS_SUPPORTED[-1]!r}).  Either bump "
        f"both together or rename one of the constants."
    )


def test_avh_14_error_detail_names_supported_range(client) -> None:
    """AVH_14: a 400 from the version gate must name the supported
    range in the ``detail`` so an out-of-date client can update its
    constant without needing to inspect the headers separately.
    """
    resp = client.post(
        "/engine/eval",
        headers={"X-Api-Key": "ci-test-key", "X-API-Version": "999"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail", "")
    assert "supports" in detail.lower(), (
        f"error detail must mention what the server supports; got {detail!r}"
    )
    assert "999" in detail, "error detail must echo the client-sent version"
    # The supported list now contains both "1" and "2"; the detail
    # should mention what the server accepts so the client can update.
    assert "1" in detail, (
        f"error detail must list the supported range; got {detail!r}"
    )
    assert "2" in detail, (
        f"error detail must list the supported range; got {detail!r}"
    )


# ---------------------------------------------------------------------------
# PR (Lichess v2 async-import) — backward-compat pin
# ---------------------------------------------------------------------------


def test_avh_15_v1_header_still_accepted_after_bump(client) -> None:
    """AVH_15: backward compat with shipped v1 clients.

    The bump from API_VERSION='1' to '2' (Lichess v2 async-import PR)
    must NOT break clients still sending ``X-API-Version: 1``.  Both
    versions live in ``API_VERSIONS_SUPPORTED`` simultaneously; the
    legacy v1 import path still serves the old 200 + summary shape
    so the shipped Android v1 build keeps working until users
    upgrade.

    Complements ``test_avh_06_matching_header_is_accepted`` (which
    happens to also send "1") by asserting the **intent**: this is a
    backward-compat property, not an accident of which version we
    happen to call "current".
    """
    resp = client.post(
        "/engine/eval",
        headers={"X-Api-Key": "ci-test-key", "X-API-Version": "1"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert resp.status_code == 200, (
        f"v1 header must remain accepted while '1' is in API_VERSIONS_SUPPORTED; "
        f"got {resp.status_code}: {resp.text!r}"
    )
    # Response still carries the server's preferred version (=='2') —
    # that's how a v1 client learns it should upgrade.
    assert resp.headers.get("X-API-Version") == "2"
