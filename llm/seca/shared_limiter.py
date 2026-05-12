"""Shared slowapi Limiter with a trusted-proxy-aware key function.

Imported by ``server.py`` (registered on ``app.state``) and any router that
needs rate-limit decorators, so all limits share the same counter backend.

The key function honours ``TRUSTED_PROXIES`` so that a deployment behind
Caddy (or any reverse proxy) keys per-client rate limits on the real client
IP, not on the proxy's loopback peer. Without this, every request behind the
proxy shares a single bucket — a single attacker can saturate the
``/auth/login`` 10/min budget for every other user.

The contract is pinned by ``llm/tests/test_security_proxy_aware_limiter.py``
(TPA_01..TPA_14). Read that file when changing behaviour — it is the spec.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import List, Union

from slowapi import Limiter
from starlette.requests import Request

logger = logging.getLogger(__name__)

_AnyNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def _parse_trusted_proxies(raw: str) -> List[_AnyNetwork]:
    """Parse a comma-separated list of IPs / CIDRs.

    Malformed entries are dropped (logged at WARNING); empty / whitespace
    tokens are tolerated silently. Bare IPs are accepted as /32 or /128.
    """
    networks: List[_AnyNetwork] = []
    for raw_token in raw.split(","):
        token = raw_token.strip()
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXIES: dropping malformed entry")
    return networks


def _load_trusted_networks() -> List[_AnyNetwork]:
    raw = os.environ.get("TRUSTED_PROXIES", "").strip()
    env = os.environ.get("SECA_ENV", "dev").strip().lower()
    if raw:
        return _parse_trusted_proxies(raw)
    if env == "prod":
        # Documented prod default: empty trust list + warning. A misconfig
        # collapses every bucket onto the immediate peer (Caddy) but never
        # silently grants trust to spoofed XFF headers.
        logger.warning(
            "TRUSTED_PROXIES unset in prod — X-Forwarded-For will never be "
            "trusted; per-client rate limits will collapse onto the "
            "reverse-proxy peer. Set TRUSTED_PROXIES to the proxy CIDR."
        )
        return []
    # Dev default: loopback only, so a local proxy works without extra config.
    return [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
    ]


_TRUSTED_NETWORKS: List[_AnyNetwork] = _load_trusted_networks()


def _ip_is_trusted(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in _TRUSTED_NETWORKS:
        try:
            if addr in net:
                return True
        except TypeError:
            # Cross-family containment check (IPv4 addr vs IPv6 net or vice
            # versa) raises in some Python versions; treat as not-in-net.
            continue
    return False


def proxy_aware_remote_address(request: Request) -> str:
    """Return the rate-limit key (real client IP) honouring TRUSTED_PROXIES.

    Algorithm:

    1. If the immediate TCP peer is NOT in ``TRUSTED_PROXIES`` → return the
       peer. An anonymous caller cannot spoof XFF to escape their bucket.
    2. Otherwise walk ``X-Forwarded-For`` right-to-left, skipping entries
       that are themselves trusted proxies. The first untrusted entry is
       the real client IP.
    3. If XFF is missing or every entry is a trusted proxy, fall back to
       the immediate peer.

    Malformed XFF tokens (anything ``ipaddress.ip_address`` cannot parse)
    are skipped during the walk — they are never returned as a key, since
    a non-IP token cannot meaningfully be either a proxy or a client IP.
    """
    peer = request.client.host if request.client else ""
    if not peer:
        return ""
    if not _ip_is_trusted(peer):
        return peer

    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return peer

    tokens = [raw.strip() for raw in xff.split(",") if raw.strip()]
    for token in reversed(tokens):
        try:
            ipaddress.ip_address(token)
        except ValueError:
            continue
        if not _ip_is_trusted(token):
            return token

    # Degenerate: every entry was a trusted proxy.
    return peer


limiter = Limiter(key_func=proxy_aware_remote_address)
