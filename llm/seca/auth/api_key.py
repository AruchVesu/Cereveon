"""Shared X-Api-Key verifier for the FastAPI app.

``llm/server.py`` (the production coaching API) imports
``verify_api_key`` from this module as the single source of truth for
X-Api-Key authentication; the env-var resolution lives here too so
the dev/prod gating is identical across every protected route.

Originally this module existed to deduplicate the verifier between
``server.py`` and the standalone ``host_app.py`` debug server.  The
debug server was retired in 2026-05-12 (host_app retirement pass);
the SN_01 / SN_01b AST-pinning tests in
``test_security_new_findings.py`` that prevented drift between the
two copies are now structural pins on the single remaining caller.

Behaviour
---------
- **Dev mode + insecure flag**
  (``SECA_API_KEY`` unset, ``SECA_ENV != prod``,
  ``SECA_INSECURE_DEV in {1, true, yes}``): pass-through.  Logs a loud
  startup warning so it can never run silently.  This is the historical
  "no key configured = open" semantic for local development, now gated
  behind an explicit opt-in flag — see ``docs/THREAT_MODEL.md`` § T6.

- **Dev mode without the insecure flag** (``SECA_API_KEY`` unset,
  ``SECA_ENV != prod``, ``SECA_INSECURE_DEV`` unset/false): HTTP 401 at
  request time.  This closes the prod-misdeployment footgun where a
  ``SECA_ENV=dev`` deploy that lands by accident on a public host would
  otherwise serve every protected endpoint without authentication.

- **Prod mode** (``SECA_ENV in {prod, production}``) with no
  ``SECA_API_KEY``: HTTP 500 at request time.  ``server.py`` additionally
  hard-fails at module import via its own ``if IS_PROD and API_KEY is
  None: raise RuntimeError`` block, so a misconfigured production
  deployment never even starts; this request-time 500 is a defensive
  belt-and-braces in case that startup guard is ever bypassed.

- **Configured key**: constant-time comparison via ``hmac.compare_digest``.
  Mismatched keys yield HTTP 401, regardless of dev/prod or the
  insecure-dev flag — the explicit flag only matters when no key is set
  at all.

The ``hmac.compare_digest`` choice (vs ``==``) is enforced by the SN_01
test in ``test_security_new_findings.py`` — replacing it would
reintroduce a one-character-at-a-time timing oracle on the API key.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

_API_KEY = os.getenv("SECA_API_KEY")
_IS_PROD = os.getenv("SECA_ENV", "dev") in {"prod", "production"}
_INSECURE_DEV = os.getenv("SECA_INSECURE_DEV", "").strip().lower() in {"1", "true", "yes"}


# Module-load warning so the loud bypass message lands in startup logs once
# per process — distinct from the per-request 401 below, which protects the
# uncommon case where SECA_INSECURE_DEV is *not* set yet someone calls a
# protected endpoint.
if (not _IS_PROD) and _API_KEY is None and _INSECURE_DEV:
    logger.warning(
        "AUTH BYPASS ACTIVE — SECA_INSECURE_DEV is set and no SECA_API_KEY "
        "is configured.  Every X-Api-Key-protected endpoint will accept "
        "ANY value (including the empty string).  This MUST NOT be used "
        "outside local development; see docs/THREAT_MODEL.md § T6."
    )


def verify_api_key(x_api_key: str = Header(None)) -> None:
    """FastAPI dependency: validate the X-Api-Key header in constant time."""
    if _API_KEY is None:
        if _IS_PROD:
            raise HTTPException(status_code=500, detail="Server misconfiguration")
        if _INSECURE_DEV:
            return  # dev mode — unauthenticated access explicitly enabled
        # Dev mode without the explicit insecure flag: reject.  This closes
        # the SECA_ENV=dev-in-production footgun (T6 in docs/THREAT_MODEL.md).
        raise HTTPException(
            status_code=401,
            detail=(
                "Unauthorized — no SECA_API_KEY is configured.  Set "
                "SECA_API_KEY to enforce auth, or set SECA_INSECURE_DEV=true "
                "for an explicit local-development bypass."
            ),
        )
    if not hmac.compare_digest(x_api_key or "", _API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")
