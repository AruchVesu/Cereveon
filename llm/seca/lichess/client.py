"""Lichess public-API client.

Wraps the two endpoints needed by the importer:

* ``GET /api/user/{username}``           — single-shot JSON profile.
* ``GET /api/games/user/{username}``     — NDJSON-streamed games list.

The client is intentionally **sync httpx** (not async): it mirrors the
established DeepSeek pattern in ``explain_pipeline.py`` and runs from
sync FastAPI route handlers via Starlette's threadpool, so it never
blocks the event loop.

Trust-boundary note (architecture)
----------------------------------
Lichess can attach its own Stockfish evaluations when called with
``evals=true``.  Those are third-party engine output, NOT trusted under
``docs/ARCHITECTURE.md`` — only the local engine pool produces ESV.  The
games endpoint here therefore pins ``evals=false`` and the import service
never propagates any Lichess-derived eval into ``GameEvent`` fields read
by the Mode-2 pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterator

import httpx

logger = logging.getLogger(__name__)

LICHESS_API_BASE = os.getenv("LICHESS_API_BASE", "https://lichess.org")
LICHESS_USER_AGENT = os.getenv("LICHESS_USER_AGENT", "ChessCoach/1.0 (lichess-import)")

# Username shape Lichess accepts (mirrors the signup-form rule).
# Validation is enforced at the router layer *and* at the client entry
# points below — defense in depth, so a future internal caller that
# bypasses the router cannot smuggle a path-traversal / SSRF payload
# (``alice/../admin``, ``evil.com?``) into the URL.  CodeQL flagged
# this as "partial server-side request forgery" on the first cut where
# only the router validated.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,30}$")


def _validated_username(raw: str) -> str:
    """Return the stripped username if it matches the Lichess shape; else raise.

    Raises ``ValueError`` rather than a custom error type because this
    is a programming-error guard, not an upstream-failure mode: any
    call that reaches the client with a non-conforming handle has
    skipped the router-layer validation that should have caught it.
    """
    if not isinstance(raw, str):
        raise ValueError("username must be a string")
    candidate = raw.strip()
    if not _USERNAME_RE.fullmatch(candidate):
        raise ValueError("username must be 2-30 chars of letters, digits, '_' or '-'")
    return candidate


# Optional bearer token for raised rate limits.  Anonymous requests are
# supported by Lichess but capped lower; a personal-access token bumps
# the per-IP quota.  When absent (default), the client sends no Authz
# header — the import service still functions, just slower under load.
LICHESS_OAUTH_TOKEN = os.getenv("LICHESS_OAUTH_TOKEN", "").strip()

# Defensive caps independent of the ``max`` games parameter.  A
# misbehaving / hostile Lichess proxy that ignored ``max`` and streamed
# forever would otherwise pin a worker.  50 MB ≈ 5000 typical games of
# PGN-in-JSON; well above any sane single import slice.
_MAX_STREAM_BYTES = 50 * 1024 * 1024
_MAX_NDJSON_LINE_BYTES = 256 * 1024  # ~10x typical game; bounds memory per game

# Hard upper bound the *caller* may request.  The router additionally
# caps at a smaller MVP value; this constant exists so the client can
# refuse a programming error before issuing a request.
MAX_GAMES_PER_REQUEST = 300


class LichessClientError(Exception):
    """Base class for client-surfaced errors."""


class LichessUserNotFound(LichessClientError):
    """Lichess returned 404 for the requested username."""


class LichessRateLimited(LichessClientError):
    """Lichess returned 429.  ``retry_after`` is seconds when known."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LichessUpstreamError(LichessClientError):
    """Lichess returned 5xx or a non-success status we don't special-case."""


class LichessParseError(LichessClientError):
    """Lichess returned non-JSON / malformed NDJSON in a streaming chunk."""


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"User-Agent": LICHESS_USER_AGENT}
    if LICHESS_OAUTH_TOKEN:
        h["Authorization"] = f"Bearer {LICHESS_OAUTH_TOKEN}"
    return h


def _raise_for_status(response: httpx.Response, *, context: str) -> None:
    """Translate Lichess HTTP errors into our typed exceptions.

    Must be called *before* the caller starts consuming a streaming
    body, but ``httpx.stream`` already gives us headers/status before
    the first chunk is read.  For the games endpoint we pass the
    in-flight ``Response`` from inside the ``with httpx.stream`` block.
    """
    if response.status_code == 404:
        raise LichessUserNotFound(f"{context}: user not found")
    if response.status_code == 429:
        retry = response.headers.get("Retry-After")
        try:
            retry_after_int = int(retry) if retry else None
        except (TypeError, ValueError):
            retry_after_int = None
        raise LichessRateLimited(f"{context}: rate limited by Lichess", retry_after=retry_after_int)
    if response.status_code >= 500:
        raise LichessUpstreamError(f"{context}: upstream {response.status_code}")
    if response.status_code >= 400:
        # 4xx that isn't 404/429 — usually a malformed parameter we
        # constructed.  Surface as upstream-error so the route returns
        # 502 rather than blaming the caller.
        raise LichessUpstreamError(f"{context}: unexpected status {response.status_code}")


# ---------------------------------------------------------------------------
# OAuth — "Sign in with Lichess" (authorization-code + PKCE, RFC 7636)
# ---------------------------------------------------------------------------

# Public identifiers for the OAuth client.  Lichess supports unregistered
# public clients: any client_id is accepted, PKCE (S256) is mandatory, and
# there is no client secret.  Both values MUST byte-match what the mobile
# app sent in its authorization request or the code exchange fails; they are
# env-overridable only for staging against a Lichess test instance.  The
# Android mirror constants live in ``LichessOAuth.kt``.
LICHESS_OAUTH_CLIENT_ID = os.getenv("LICHESS_OAUTH_CLIENT_ID", "ai.chesscoach.app")
LICHESS_OAUTH_REDIRECT_URI = os.getenv(
    "LICHESS_OAUTH_REDIRECT_URI", "ai.chesscoach.app://lichess-auth"
)

# RFC 7636 §4.1 code-verifier shape: 43-128 chars of the unreserved set.
CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")

# Authorization codes are opaque strings minted by Lichess; bound to
# printable ASCII with a hard length cap so a hostile caller cannot smuggle
# control bytes or megabyte payloads toward the upstream exchange.
AUTH_CODE_RE = re.compile(r"^[\x21-\x7e]{1,512}$")

# Access tokens returned by the exchange — same defensive shape check
# before the value is placed into an Authorization header.
_ACCESS_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,512}$")

_OAUTH_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


class LichessOAuthError(LichessClientError):
    """Lichess rejected the OAuth grant — invalid / expired / replayed
    code, PKCE verifier mismatch, or a token it no longer accepts.  Maps
    to 401 at the route layer (the client must restart the flow)."""


def exchange_authorization_code(code: str, code_verifier: str) -> str:
    """Exchange an OAuth authorization code for a Lichess access token.

    ``POST /api/token`` with the PKCE ``code_verifier`` (public client —
    no secret).  ``redirect_uri`` and ``client_id`` are the pinned module
    constants and must match the app's authorization request exactly.

    The exchange happens server-side by design: the mobile app forwards
    ``code`` + ``code_verifier`` to ``POST /auth/lichess`` instead of
    exchanging locally, so Lichess access tokens never live on the device
    and a token minted for a different app cannot be replayed into a
    sign-in (only a fresh authorization flow for OUR client_id works).

    Returns the access token string.  Raises ``LichessOAuthError`` on a
    4xx grant rejection, ``LichessRateLimited`` on 429,
    ``LichessUpstreamError`` on 5xx / transport failure, and
    ``LichessParseError`` when the token is absent or malformed.
    """
    if not isinstance(code, str) or not AUTH_CODE_RE.fullmatch(code):
        raise LichessOAuthError("malformed authorization code")
    if not isinstance(code_verifier, str) or not CODE_VERIFIER_RE.fullmatch(code_verifier):
        raise LichessOAuthError("malformed code_verifier")

    url = f"{LICHESS_API_BASE}/api/token"
    try:
        with httpx.Client(timeout=_OAUTH_TIMEOUT) as client:
            response = client.post(
                url,
                # Deliberately NOT _headers(): the server's optional
                # LICHESS_OAUTH_TOKEN must never ride along on a token
                # exchange performed on behalf of a user.
                headers={"User-Agent": LICHESS_USER_AGENT},
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": LICHESS_OAUTH_REDIRECT_URI,
                    "client_id": LICHESS_OAUTH_CLIENT_ID,
                },
            )
    except httpx.HTTPError as exc:
        raise LichessUpstreamError(f"token exchange failed: {exc}") from exc

    if response.status_code == 429:
        retry = response.headers.get("Retry-After")
        try:
            retry_after_int = int(retry) if retry else None
        except (TypeError, ValueError):
            retry_after_int = None
        raise LichessRateLimited("token exchange: rate limited", retry_after=retry_after_int)
    if response.status_code >= 500:
        raise LichessUpstreamError(f"token exchange: upstream {response.status_code}")
    if response.status_code >= 400:
        # Grant rejection (invalid_grant & friends).  The body names the
        # OAuth error; don't propagate detail to callers — a per-reason
        # message would hand probing clients an oracle.
        raise LichessOAuthError(f"token exchange rejected ({response.status_code})")

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LichessParseError(f"token response was not JSON: {exc}") from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not _ACCESS_TOKEN_RE.fullmatch(token):
        raise LichessParseError("token response missing or malformed access_token")
    return token


def fetch_account(access_token: str) -> dict:
    """Fetch the authenticated Lichess account for an OAuth access token.

    ``GET /api/account`` with the USER's token (never the server's
    optional ``LICHESS_OAUTH_TOKEN``).  The response is the same
    public-profile shape as :func:`fetch_user_profile`, so the OAuth
    sign-in flow can hand it to ``import_service.link_account`` as a
    pre-fetched profile.

    The ``id`` field is validated against the Lichess username shape
    before returning: it becomes a database identity key downstream
    (``players.lichess_user_id``), so a compromised or spoofed upstream
    must not be able to inject an arbitrary string.  Raises
    ``LichessParseError`` when absent or malformed (fail closed).
    """
    if not isinstance(access_token, str) or not _ACCESS_TOKEN_RE.fullmatch(access_token):
        raise LichessOAuthError("malformed access token")

    url = f"{LICHESS_API_BASE}/api/account"
    headers = {
        "User-Agent": LICHESS_USER_AGENT,
        "Authorization": f"Bearer {access_token}",
    }
    try:
        with httpx.Client(timeout=_OAUTH_TIMEOUT) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise LichessUpstreamError(f"account fetch failed: {exc}") from exc

    if response.status_code == 401:
        raise LichessOAuthError("account fetch: token rejected")
    _raise_for_status(response, context="account")

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LichessParseError(f"account body was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LichessParseError("account body was not a JSON object")

    canonical_id = payload.get("id")
    if not isinstance(canonical_id, str) or not _USERNAME_RE.fullmatch(canonical_id):
        raise LichessParseError("account response missing or malformed 'id'")
    return payload


def revoke_token(access_token: str) -> None:
    """Best-effort ``DELETE /api/token`` — revoke an OAuth access token.

    The sign-in flow only needs the token long enough to prove identity;
    holding a live credential we never use again is pure liability, so
    the auth route revokes immediately after the account fetch.  Never
    raises: revocation failure must not fail a sign-in whose identity is
    already verified.
    """
    if not isinstance(access_token, str) or not _ACCESS_TOKEN_RE.fullmatch(access_token):
        return
    url = f"{LICHESS_API_BASE}/api/token"
    headers = {
        "User-Agent": LICHESS_USER_AGENT,
        "Authorization": f"Bearer {access_token}",
    }
    try:
        with httpx.Client(timeout=_OAUTH_TIMEOUT) as client:
            client.delete(url, headers=headers)
    except httpx.HTTPError:
        logger.warning("lichess token revocation failed (ignored)", exc_info=True)


def fetch_user_profile(username: str) -> dict:
    """Fetch a single Lichess user's public profile.

    Returns the parsed JSON dict.  The shape we depend on downstream is
    documented in ``import_service._calibration_from_profile`` — this
    function does not trim or transform the response.
    """
    safe_username = _validated_username(username)

    url = f"{LICHESS_API_BASE}/api/user/{safe_username}"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
        ) as client:
            response = client.get(url, headers=_headers())
    except httpx.HTTPError as exc:
        raise LichessUpstreamError(f"profile fetch failed: {exc}") from exc

    _raise_for_status(response, context="profile")

    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LichessParseError(f"profile body was not JSON: {exc}") from exc


def fetch_user_games(
    username: str,
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
    max_games: int | None = None,
    rated: bool = True,
    perf_types: list[str] | None = None,
) -> Iterator[dict]:
    """Stream a Lichess user's games as NDJSON, yielding parsed dicts.

    Lichess returns one JSON object per line when called with
    ``Accept: application/x-ndjson``.  We further set ``pgnInJson=true``
    so each line carries a ``pgn`` field with the standard PGN payload.

    The caller MUST iterate the generator fully (or break early) — the
    underlying HTTP connection is held open inside the
    ``with httpx.stream`` block and closes when the generator exits.

    Trust-boundary pin: ``evals=false`` is hardcoded.  Lichess's own
    Stockfish output is never imported.

    Parameters
    ----------
    username
        Lichess handle (case-insensitive on their end).
    since_ms / until_ms
        Unix epoch milliseconds.  ``since_ms`` is the watermark for
        incremental imports.
    max_games
        Hard upper bound on games returned.  ``None`` means no client
        cap (Lichess's own default applies).  The caller — typically
        the import router — should always pass an explicit cap.
    rated
        Filter to rated games only.  Default True keeps casual / random
        games out of the dataset used for calibration / coaching.
    perf_types
        Subset of ``{"ultraBullet","bullet","blitz","rapid","classical",
        "correspondence","chess960","crazyhouse","antichess","atomic",
        "horde","kingOfTheHill","racingKings","threeCheck"}`` — defaults
        to None (no filter).  The importer usually passes
        ``["blitz", "rapid", "classical"]``.

    Yields
    ------
    dict
        One parsed NDJSON line per Lichess game.  See Lichess docs for
        the exact field set; the importer only depends on
        ``id``, ``pgn``, ``players``, ``winner``, ``createdAt``,
        ``rated``, ``speed``, ``perf``.
    """
    safe_username = _validated_username(username)
    if max_games is not None:
        if max_games <= 0:
            raise ValueError("max_games must be positive")
        if max_games > MAX_GAMES_PER_REQUEST:
            raise ValueError(f"max_games {max_games} exceeds client cap {MAX_GAMES_PER_REQUEST}")

    params: dict[str, str] = {
        "pgnInJson": "true",
        "clocks": "true",
        "opening": "true",
        # Architecture pin: Lichess evals are untrusted third-party
        # engine output and must never enter the ESV pipeline.  See
        # docs/ARCHITECTURE.md.
        "evals": "false",
    }
    if rated:
        params["rated"] = "true"
    if since_ms is not None:
        params["since"] = str(since_ms)
    if until_ms is not None:
        params["until"] = str(until_ms)
    if max_games is not None:
        params["max"] = str(max_games)
    if perf_types:
        params["perfType"] = ",".join(perf_types)

    url = f"{LICHESS_API_BASE}/api/games/user/{safe_username}"
    headers = {**_headers(), "Accept": "application/x-ndjson"}

    try:
        with httpx.stream(
            "GET",
            url,
            params=params,
            headers=headers,
            # Per-read 60s budget; the full stream may take longer for
            # large imports — that's fine, only individual reads have
            # to complete within the read timeout.
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
        ) as response:
            _raise_for_status(response, context="games")

            bytes_seen = 0
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                # Defensive: cap per-line and total bytes to bound
                # memory under a hostile / misbehaving upstream.
                if len(line) > _MAX_NDJSON_LINE_BYTES:
                    raise LichessParseError(f"NDJSON line exceeded {_MAX_NDJSON_LINE_BYTES} bytes")
                bytes_seen += len(line)
                if bytes_seen > _MAX_STREAM_BYTES:
                    raise LichessParseError(f"stream exceeded {_MAX_STREAM_BYTES} bytes; aborting")
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LichessParseError(f"malformed NDJSON line: {exc}") from exc
    except httpx.HTTPError as exc:
        # Network errors (connect/read timeout, DNS failure, etc.)
        # surface as upstream errors so the route layer returns 502.
        raise LichessUpstreamError(f"games stream failed: {exc}") from exc
