"""Google Play billing verification — the Pro-activation surface.

``POST /billing/google/verify``: the Android client completes a Play
Billing purchase, then posts the ``purchase_token`` + ``product_id``
here.  The server verifies the token against the Google Play Developer
API (``purchases.subscriptionsv2.get``) using service-account
credentials from env and, only on an entitled verdict, flips the
player's plan through ``entitlements.set_plan``.  The client's claim is
never trusted — a forged or replayed token flips nothing.

Verification seam
-----------------
``_verify_google_purchase(purchase_token, product_id)`` is the single
injectable seam: tests monkeypatch it with a fake verdict, so CI makes
no external calls; production resolves credentials from env at call
time.  When the three ``GOOGLE_PLAY_*`` vars are unset the seam raises
``BillingNotConfiguredError`` → HTTP 503 — shipping this router without
credentials is safe and LOUD, never fake-success.

Entitled states
---------------
``SUBSCRIPTION_STATE_ACTIVE`` and ``_IN_GRACE_PERIOD`` are obviously
entitled.  ``_CANCELED`` is too: in subscriptionsv2 it means auto-renew
was turned off but the paid period has not ended — access runs until
expiry, at which point the state becomes ``_EXPIRED`` (the terminal
loss).  Expiry-driven automatic downgrade is the RTDN follow-up, not
this endpoint.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from llm.seca.auth.router import get_current_player, get_db
from llm.seca.entitlements import service as entitlements
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

#: Play products this server recognises → the plan they grant.  Must
#: stay in lock-step with the Android paywall's product catalogue
#: (PaywallActivity PLAY_PRODUCT_IDS: monthly → pro_monthly, yearly →
#: pro_yearly) and the ``upgrade.product`` hint in the chat 402 body
#: (API_CONTRACTS.md §5).  Both products grant the same "pro" plan —
#: the billing period is a Play-side pricing concern, not an
#: entitlement distinction.
KNOWN_PRODUCTS: dict[str, str] = {"pro_monthly": "pro", "pro_yearly": "pro"}

#: subscriptionsv2 states that still carry entitlement — see the module
#: docstring for the CANCELED rationale.
_ENTITLED_STATES = frozenset(
    {
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        "SUBSCRIPTION_STATE_CANCELED",
    }
)

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_HTTP_TIMEOUT_SECONDS = 15


class BillingNotConfiguredError(RuntimeError):
    """GOOGLE_PLAY_* service-account env vars are absent."""


class BillingUpstreamError(RuntimeError):
    """Google's OAuth or Play API answered abnormally (network, 5xx, parse)."""


@dataclass(frozen=True)
class PurchaseVerdict:
    """Outcome of one token verification."""

    entitled: bool
    state: str


class VerifyGooglePurchaseRequest(BaseModel):
    """Body of POST /billing/google/verify."""

    purchase_token: str
    product_id: str

    @field_validator("purchase_token")
    @classmethod
    def validate_purchase_token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("purchase_token must not be empty")
        # Play tokens are opaque base64-ish strings well under this cap;
        # the bound is a defensive ceiling, not a format claim.
        if len(v) > 600:
            raise ValueError("purchase_token too long (max 600 chars)")
        if any(c < "\x20" for c in v):
            raise ValueError("purchase_token contains control characters")
        return v

    @field_validator("product_id")
    @classmethod
    def validate_product_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product_id must not be empty")
        if len(v) > 64:
            raise ValueError("product_id too long (max 64 chars)")
        return v


def _sa_credentials() -> tuple[str, str, str]:
    """(package_name, client_email, private_key_pem) from env, or raise.

    ``GOOGLE_PLAY_SA_PRIVATE_KEY`` commonly arrives with literal ``\\n``
    sequences when pasted into a .env file; normalise them back to real
    newlines so the PEM parses.
    """
    package = os.getenv("GOOGLE_PLAY_PACKAGE_NAME", "").strip()
    email = os.getenv("GOOGLE_PLAY_SA_EMAIL", "").strip()
    key = os.getenv("GOOGLE_PLAY_SA_PRIVATE_KEY", "").strip()
    if not package or not email or not key:
        raise BillingNotConfiguredError(
            "GOOGLE_PLAY_PACKAGE_NAME / GOOGLE_PLAY_SA_EMAIL / "
            "GOOGLE_PLAY_SA_PRIVATE_KEY must all be set for purchase verification"
        )
    return package, email, key.replace("\\n", "\n")


def _google_access_token(email: str, private_key_pem: str) -> str:
    """Service-account JWT-bearer grant → short-lived OAuth access token.

    Signed RS256 via python-jose (its pure-python ``rsa`` backend — no
    ``cryptography`` wheel dependency), exchanged at Google's token
    endpoint.  Raises ``BillingUpstreamError`` on any transport or
    protocol failure.
    """
    from jose import jwt as _jose_jwt  # noqa: PLC0415  # billing-only dependency path

    now = int(time.time())
    assertion = _jose_jwt.encode(
        {
            "iss": email,
            "scope": _ANDROID_PUBLISHER_SCOPE,
            "aud": _OAUTH_TOKEN_URL,
            "iat": now,
            "exp": now + 600,
        },
        private_key_pem,
        algorithm="RS256",
    )
    try:
        resp = httpx.post(
            _OAUTH_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token", "")
    except httpx.HTTPError as exc:
        raise BillingUpstreamError(f"OAuth token exchange failed: {exc}") from exc
    except ValueError as exc:  # json decode
        raise BillingUpstreamError("OAuth token exchange returned non-JSON") from exc
    if not token:
        raise BillingUpstreamError("OAuth token exchange returned no access_token")
    return token


def _verify_google_purchase(purchase_token: str, product_id: str) -> PurchaseVerdict:
    """The injectable verification seam — ``purchases.subscriptionsv2.get``.

    Returns a ``PurchaseVerdict`` for answerable outcomes (including
    Google explicitly rejecting the token: 400/404/410 → not entitled);
    raises ``BillingNotConfiguredError`` / ``BillingUpstreamError`` for
    states where no verdict exists.  ``product_id`` is accepted for
    parity with future per-product checks; entitlement is decided by
    the subscription state Google reports for the token.
    """
    package, email, key = _sa_credentials()
    access_token = _google_access_token(email, key)
    url = (
        "https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/purchases/subscriptionsv2/tokens/{purchase_token}"
    )
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise BillingUpstreamError(f"Play API request failed: {exc}") from exc
    if resp.status_code in (400, 404, 410):
        # Google affirmatively rejected the token — a verdict, not an outage.
        return PurchaseVerdict(entitled=False, state=f"rejected_http_{resp.status_code}")
    if resp.status_code != 200:
        raise BillingUpstreamError(f"Play API answered HTTP {resp.status_code}")
    try:
        state = resp.json().get("subscriptionState", "SUBSCRIPTION_STATE_UNSPECIFIED")
    except ValueError as exc:
        raise BillingUpstreamError("Play API returned non-JSON") from exc
    return PurchaseVerdict(entitled=state in _ENTITLED_STATES, state=state)


@router.post("/google/verify")
@limiter.limit("10/minute")
async def verify_google_purchase(
    req: VerifyGooglePurchaseRequest,
    request: Request,
    player=Depends(get_current_player),
    db=Depends(get_db),
):
    """Verify a Play purchase token and activate the purchased plan.

    Owner-scoped by construction: the plan flip targets the
    authenticated ``player`` only — the body carries no player
    identity.  ``entitlements.set_plan`` re-raises after rollback on
    persistence failure, so a 200 is only ever returned for a landed
    flip (no fake success), and any DB failure surfaces as a 500 with
    the plan unchanged.
    """
    plan = KNOWN_PRODUCTS.get(req.product_id)
    if plan is None:
        raise HTTPException(status_code=400, detail="unknown product_id")

    try:
        verdict = await asyncio.to_thread(
            _verify_google_purchase, req.purchase_token, req.product_id
        )
    except BillingNotConfiguredError as exc:
        logger.warning("billing verify called but not configured: %s", exc)
        raise HTTPException(
            status_code=503, detail="purchase verification not configured"
        ) from exc
    except BillingUpstreamError as exc:
        logger.warning("billing verify upstream failure: %s", exc)
        raise HTTPException(
            status_code=502, detail="purchase verification temporarily unavailable"
        ) from exc

    if not verdict.entitled:
        # A real verdict from Google that this token carries no
        # entitlement — expired, refunded, revoked, or never real.
        raise HTTPException(
            status_code=402, detail=f"purchase not active ({verdict.state})"
        )

    entitlements.set_plan(db, player, plan)
    logger.info(
        "billing: player %s activated plan %s via %s (state %s)",
        player.id,
        plan,
        req.product_id,
        verdict.state,
    )
    return {"plan": plan, "product_id": req.product_id, "state": verdict.state}
