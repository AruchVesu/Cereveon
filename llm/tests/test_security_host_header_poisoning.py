"""
Host-header poisoning regression — llm/tests/test_security_host_header_poisoning.py

Pins the fix for the starlette "BadHost" advisory:

    CVE-2026-48710 / GHSA-86qp-5c8j-p5mr / PYSEC-2026-161
    "Missing Host header validation poisons request.url.path,
     bypassing path-based security checks"
    Affected: starlette < 1.0.1   Fixed: starlette 1.0.1 (PR encode/starlette#3279)

The bug: starlette < 1.0.1 reconstructs ``request.url`` from the inbound
``Host`` header WITHOUT validating it.  A ``Host`` value containing ``/``,
``?``, ``#``, ``@``, a backslash, or a space lets an attacker inject the
authority/path boundary, so ``request.url.path`` (the reconstructed path)
diverges from ``scope["path"]`` (the path FastAPI actually routes on).  Any
middleware that makes a decision from ``request.url.path`` — an allow-list
exemption, an auth skip, a metrics filter — can then be steered by a header
the attacker controls.

This backend reads ``request.url.path`` in exactly that shape in
``llm/server.py``:

  * ``api_version_gate``        — ``is_discovery = request.url.path in _DISCOVERY_PATHS``
                                  (a path-allow-list that *exempts* a route
                                  from the X-API-Version mismatch gate)
  * ``prometheus_http_middleware`` — ``request.url.path == _METRICS_PATH``

The fix (starlette 1.0.1) validates the ``Host`` header against a strict
regex and *silently ignores* a malformed value, falling back to the trusted
ASGI ``server`` tuple — so ``request.url`` can no longer be attacker-steered.

These tests assert the post-fix behaviour directly.  They are RED on the
vulnerable starlette 0.50.0 this repo previously pinned and GREEN on the
1.2.1 it now pins, so they double as a regression guard against anyone
re-pinning starlette back into the vulnerable range.

Stable test IDs (do NOT rename):
  BADHOST_01  malformed Host cannot change request.url.path (parametrised)
  BADHOST_02  malformed Host cannot change request.url host (parametrised)
  BADHOST_03  a valid Host is still honoured (no over-correction)
  BADHOST_04  absent Host falls back to the trusted ASGI server tuple
  BADHOST_05  genuine discovery route is exempt (gate control)
  BADHOST_06  genuine protected route is gated (gate control)
  BADHOST_07  poisoned Host cannot forge a discovery-path exemption
  BADHOST_08  installed starlette is past the BadHost fix (>= 1.0.1)
"""

from __future__ import annotations

import asyncio
import re

import pytest
from starlette.requests import Request

# The ASGI ``server`` tuple is the trusted origin starlette must fall back to
# whenever the Host header is missing or malformed.  Picking a non-default
# port (8000, not 80) also exercises the explicit-port fallback branch.
_TRUSTED_HOST = "backend.internal"
_TRUSTED_SERVER = (_TRUSTED_HOST, 8000)
_ROUTED_PATH = "/inference/move"

# Each value is rejected by starlette 1.0.1's host regex because it carries a
# character that breaks the authority/path boundary.  On the vulnerable
# release at least one of (reconstructed path, reconstructed host) is steered
# away from the trusted origin; on the fixed release both fall back to it.
_POISON_HOSTS = [
    "evil.example/seca/status",  # slash → injects a leading path segment
    "evil.example/admin",
    "victim.test/../secret",
    "evil.example?leak=1",  # query delimiter
    "evil.example#frag",  # fragment delimiter
    "user@evil.example",  # userinfo delimiter
    "evil.example\\admin",  # backslash
    "host with spaces",  # whitespace
]


def _http_scope(path: str, host_header: str | None) -> dict:
    """Build a minimal ASGI ``http`` scope with an optional Host header."""
    headers: list[tuple[bytes, bytes]] = []
    if host_header is not None:
        headers.append((b"host", host_header.encode("latin-1")))
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "server": _TRUSTED_SERVER,
        "client": ("203.0.113.7", 54321),
    }


# ---------------------------------------------------------------------------
# Layer 1 — the keystone invariant every ``request.url.path`` read relies on.
# ---------------------------------------------------------------------------
class TestHostHeaderCannotPoisonRequestUrl:
    @pytest.mark.parametrize("host", _POISON_HOSTS)
    def test_malformed_host_does_not_change_path(self, host: str) -> None:
        """BADHOST_01 — a malformed Host leaves request.url.path == scope path."""
        request = Request(_http_scope(_ROUTED_PATH, host))
        assert request.url.path == _ROUTED_PATH, (
            f"Host {host!r} poisoned request.url.path to "
            f"{request.url.path!r} (expected {_ROUTED_PATH!r}). starlette is "
            f"vulnerable to CVE-2026-48710 (BadHost); pin starlette >= 1.0.1."
        )

    @pytest.mark.parametrize("host", _POISON_HOSTS)
    def test_malformed_host_does_not_change_hostname(self, host: str) -> None:
        """BADHOST_02 — a malformed Host falls back to the trusted server host."""
        request = Request(_http_scope(_ROUTED_PATH, host))
        assert request.url.hostname == _TRUSTED_HOST, (
            f"Host {host!r} steered request.url.hostname to "
            f"{request.url.hostname!r} (expected the trusted ASGI server "
            f"{_TRUSTED_HOST!r}). starlette is vulnerable to CVE-2026-48710."
        )

    def test_valid_host_is_still_honoured(self) -> None:
        """BADHOST_03 — the fix must not blanket-ignore every Host header."""
        request = Request(_http_scope(_ROUTED_PATH, "client.example:8000"))
        assert request.url.hostname == "client.example"
        assert request.url.path == _ROUTED_PATH

    def test_absent_host_falls_back_to_server(self) -> None:
        """BADHOST_04 — no Host header → trusted ASGI server tuple is used."""
        request = Request(_http_scope(_ROUTED_PATH, None))
        assert request.url.hostname == _TRUSTED_HOST
        assert request.url.path == _ROUTED_PATH


# ---------------------------------------------------------------------------
# Layer 2 — end-to-end: a path-allow-list gate cannot be bypassed via Host.
# Mirrors the real check in llm/server.py::api_version_gate:
#     is_discovery = request.url.path in _DISCOVERY_PATHS
# ---------------------------------------------------------------------------
# A genuinely-exempt public route ...
_DISCOVERY_PATHS = frozenset({"/seca/status"})
# ... and a route that is a path-suffix of it.  This is the generic
# precondition for the BadHost bypass: the reconstructed path can only *prepend*
# the injected Host segment, so any routed path that is a suffix of an exempt
# path can be forged into the exempt set on the vulnerable release.
_SUFFIX_ROUTE = "/status"
_POISON_FORGING_DISCOVERY = "attacker.test/seca"  # -> http://attacker.test/seca/status


def _make_discovery_gated_app(discovery: frozenset[str]):
    """A pure-ASGI app whose exemption decision reads request.url.path."""

    async def app(scope, receive, send) -> None:
        request = Request(scope, receive)
        is_discovery = request.url.path in discovery  # the vulnerable read
        if is_discovery:
            status, payload = 200, b"discovery-exempt"
        else:
            status, payload = 403, b"gated"  # stands in for the 400/auth gate
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    return app


def _drive(app, scope) -> int:
    """Invoke an ASGI app once and return its HTTP response status."""

    async def _run() -> list[dict]:
        sent: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            sent.append(message)

        await app(scope, receive, send)
        return sent

    messages = asyncio.run(_run())
    for message in messages:
        if message["type"] == "http.response.start":
            return int(message["status"])
    raise AssertionError("ASGI app sent no http.response.start")


class TestPathAllowListNotBypassable:
    def test_genuine_discovery_route_is_exempt(self) -> None:
        """BADHOST_05 — a clean request to the exempt route is allowed (control)."""
        app = _make_discovery_gated_app(_DISCOVERY_PATHS)
        status = _drive(app, _http_scope("/seca/status", _TRUSTED_HOST))
        assert status == 200

    def test_genuine_protected_route_is_gated(self) -> None:
        """BADHOST_06 — a clean request to a non-exempt route is gated (control)."""
        app = _make_discovery_gated_app(_DISCOVERY_PATHS)
        status = _drive(app, _http_scope(_SUFFIX_ROUTE, _TRUSTED_HOST))
        assert status == 403

    def test_poisoned_host_cannot_forge_exemption(self) -> None:
        """BADHOST_07 — a Host crafted to forge the exempt path must NOT bypass the gate.

        On vulnerable starlette the routed path ``/status`` is reconstructed as
        ``/seca/status`` (Host ``attacker.test/seca``), lands in the allow-list,
        and returns 200 — the bypass.  On the fixed release the malformed Host is
        ignored, the path stays ``/status``, and the gate returns 403.
        """
        app = _make_discovery_gated_app(_DISCOVERY_PATHS)
        status = _drive(app, _http_scope(_SUFFIX_ROUTE, _POISON_FORGING_DISCOVERY))
        assert status == 403, (
            "Host-header poisoning forged a discovery-path exemption and "
            "bypassed the path-based gate (CVE-2026-48710 / BadHost). "
            "starlette must be >= 1.0.1."
        )


# ---------------------------------------------------------------------------
# Layer 3 — dependency floor: lock the pin out of the vulnerable range.
# ---------------------------------------------------------------------------
def test_installed_starlette_is_past_badhost_fix() -> None:
    """BADHOST_08 — installed starlette must be >= 1.0.1 (CVE-2026-48710 fix)."""
    import starlette

    numbers = re.findall(r"\d+", starlette.__version__)
    version = tuple(int(part) for part in numbers[:3])
    assert version >= (1, 0, 1), (
        f"starlette {starlette.__version__} is inside the CVE-2026-48710 / "
        f"PYSEC-2026-161 ('BadHost') vulnerable range (< 1.0.1). Pin "
        f"starlette >= 1.0.1 in llm/requirements.txt."
    )
