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
from dataclasses import dataclass
from typing import Iterator

import chess
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

# Hard cap on OAuth response bodies (token / account / revoke) — a few KB
# in practice.  The eager ``response.json()`` pattern used by
# fetch_user_profile reads the whole body into memory before any size
# check can run; the OAuth surface runs per sign-in against an upstream we
# must treat as compromisable, so it streams with this cap instead
# (mirrors the byte-cap discipline of fetch_user_games' NDJSON reader).
_MAX_OAUTH_BODY_BYTES = 1 * 1024 * 1024


class LichessOAuthError(LichessClientError):
    """Lichess rejected the OAuth grant — invalid / expired / replayed
    code, PKCE verifier mismatch, or a token it no longer accepts.  Maps
    to 401 at the route layer (the client must restart the flow)."""


class _BoundedResponse:
    """Status + headers + size-capped body captured from a streamed request."""

    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self._body = body

    def json(self) -> object:
        return json.loads(self._body.decode("utf-8"))


def _retry_after_seconds(headers: dict[str, str]) -> int | None:
    retry = headers.get("Retry-After")
    try:
        return int(retry) if retry else None
    except (TypeError, ValueError):
        return None


def _request_json_bounded(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    data: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    max_bytes: int = _MAX_OAUTH_BODY_BYTES,
    context: str,
) -> _BoundedResponse:
    """Issue a request in streaming mode, reading at most ``max_bytes``.

    Transport failures surface as ``LichessUpstreamError``; an oversized
    body surfaces as ``LichessParseError`` (fail closed) without ever
    buffering more than the cap.  ``params`` (query string), ``timeout``,
    and ``max_bytes`` default to the OAuth-surface values so existing
    callers are unchanged; the puzzle-fetch surface overrides them with a
    shorter timeout and a smaller cap.
    """
    # Forward ``params`` only when present so the OAuth / token surfaces
    # (which pass none) keep their exact prior call shape — their test doubles
    # stub ``stream(method, url, headers, data)`` without a ``params`` kwarg.
    stream_kwargs: dict = {"headers": headers, "data": data}
    if params is not None:
        stream_kwargs["params"] = params
    try:
        with httpx.Client(timeout=timeout or _OAUTH_TIMEOUT) as client:
            with client.stream(method, url, **stream_kwargs) as response:
                chunks: list[bytes] = []
                seen = 0
                for chunk in response.iter_bytes():
                    seen += len(chunk)
                    if seen > max_bytes:
                        raise LichessParseError(
                            f"{context}: response body exceeded {max_bytes} bytes"
                        )
                    chunks.append(chunk)
                return _BoundedResponse(
                    response.status_code, dict(response.headers), b"".join(chunks)
                )
    except httpx.HTTPError as exc:
        raise LichessUpstreamError(f"{context}: request failed: {exc}") from exc


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

    response = _request_json_bounded(
        "POST",
        f"{LICHESS_API_BASE}/api/token",
        # Deliberately NOT _headers(): the server's optional
        # LICHESS_OAUTH_TOKEN must never ride along on a token exchange
        # performed on behalf of a user.
        headers={"User-Agent": LICHESS_USER_AGENT},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": LICHESS_OAUTH_REDIRECT_URI,
            "client_id": LICHESS_OAUTH_CLIENT_ID,
        },
        context="token exchange",
    )

    if response.status_code == 429:
        raise LichessRateLimited(
            "token exchange: rate limited", retry_after=_retry_after_seconds(response.headers)
        )
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
    ``LichessParseError`` when absent or malformed (fail closed).  The
    display ``username`` field is normalised the same way but leniently —
    a malformed value is REPLACED by the canonical id rather than failing
    the sign-in, so downstream consumers (the /auth/lichess response,
    link_account's calibration profile) never see an arbitrary upstream
    string in a display slot.
    """
    if not isinstance(access_token, str) or not _ACCESS_TOKEN_RE.fullmatch(access_token):
        raise LichessOAuthError("malformed access token")

    response = _request_json_bounded(
        "GET",
        f"{LICHESS_API_BASE}/api/account",
        headers={
            "User-Agent": LICHESS_USER_AGENT,
            "Authorization": f"Bearer {access_token}",
        },
        context="account",
    )

    if response.status_code == 401:
        raise LichessOAuthError("account fetch: token rejected")
    if response.status_code == 429:
        raise LichessRateLimited(
            "account fetch: rate limited", retry_after=_retry_after_seconds(response.headers)
        )
    if response.status_code >= 400:
        raise LichessUpstreamError(f"account fetch: unexpected status {response.status_code}")

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LichessParseError(f"account body was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LichessParseError("account body was not a JSON object")

    canonical_id = payload.get("id")
    if not isinstance(canonical_id, str) or not _USERNAME_RE.fullmatch(canonical_id):
        raise LichessParseError("account response missing or malformed 'id'")

    display = payload.get("username")
    if not isinstance(display, str) or not _USERNAME_RE.fullmatch(display):
        payload["username"] = canonical_id
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
    try:
        _request_json_bounded(
            "DELETE",
            f"{LICHESS_API_BASE}/api/token",
            headers={
                "User-Agent": LICHESS_USER_AGENT,
                "Authorization": f"Bearer {access_token}",
            },
            context="token revoke",
        )
    except LichessClientError:
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


# ---------------------------------------------------------------------------
# Puzzle fetch — GET /api/puzzle/next (per-mistake study-plan practice)
# ---------------------------------------------------------------------------
#
# The study-plan agent (llm/seca/coach/study_plan/) fills a mistake's day-3 /
# day-7 practice slots with puzzles that match the day-0 mistake's THEME and
# SIDE-to-move.  This surface fetches one theme-matched puzzle at a time; the
# study-plan layer loops it (varying difficulty) to collect a side-matched
# pair.  Anonymous access works; an optional LICHESS_OAUTH_TOKEN raises the
# per-IP rate limit (already threaded through _headers()).
#
# Trust boundary: the puzzle is a training POSITION, not an engine
# evaluation.  Its solver move is stored only as a display / short-circuit
# hint — whether a replay is "solved" is judged by the LOCAL engine on
# POST /training/verify-replay (llm/seca/mistakes/verify.py), never by the
# Lichess-supplied move.  Lichess's own evals are never requested here.

# Lichess "angle" theme slugs we allow into the /api/puzzle/next URL.  The
# study-plan layer maps our internal THEME_VOCABULARY onto this set; pinning
# an allowlist here (defense in depth, mirroring _validated_username) means a
# bug or a hostile internal caller cannot smuggle an arbitrary query-string /
# path payload toward the upstream request.
_PUZZLE_ANGLE_ALLOWED: frozenset[str] = frozenset(
    {"fork", "pin", "backRankMate", "hangingPiece", "exposedKing", "opening", "endgame"}
)

# Difficulty bands accepted by ?difficulty= on /api/puzzle/next.
_PUZZLE_DIFFICULTY_ALLOWED: frozenset[str] = frozenset(
    {"easiest", "easier", "normal", "harder", "hardest"}
)

# Defensive caps applied to the puzzle response before python-chess touches
# it.  A real game pgn is a few hundred bytes; these bound the work push_san()
# does under a hostile / misbehaving upstream body.
_MAX_PUZZLE_PGN_CHARS = 8192
_MAX_PUZZLE_SOLUTION_MOVES = 64
_MAX_PUZZLE_BODY_BYTES = 256 * 1024  # /api/puzzle/next is a few KB in practice

# Shorter than the OAuth timeout: this runs (looped) inside the /game/finish
# background task, so bound each call tightly.
_PUZZLE_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


@dataclass(frozen=True)
class LichessPuzzle:
    """One Lichess puzzle reduced to the solver's decision point.

    ``solver_fen`` is the position the human faces; ``side`` is the side to
    move there (the solver's colour); ``solver_move_uci`` is Lichess's first
    solution move — a display / short-circuit hint only, NOT a correctness
    oracle (the local engine judges the replay).  All three are derived and
    legality-checked at fetch time so a malformed upstream response fails
    closed instead of shipping a broken position downstream.
    """

    id: str
    rating: int
    themes: tuple[str, ...]
    solver_fen: str
    solver_move_uci: str
    side: chess.Color


def _parse_puzzle_payload(payload: object) -> LichessPuzzle:
    """Validate a /api/puzzle/next body and derive the solver position.

    Derivation (verified 2026-07-08 against 6 live puzzles, both sides): the
    ``game.pgn`` is truncated to ``initialPly + 1`` plies; replaying that many
    lands on the solver's position, where ``solution[0]`` is legal and the
    side to move is the solver's colour.  We replay ``min(len(sans),
    initialPly + 1)`` so a future full-length-pgn change can't overshoot, then
    require ``solution[0]`` to be legal — the fail-closed guard against a
    derivation drift.

    Raises ``LichessParseError`` on any structural problem (fail closed).
    """
    if not isinstance(payload, dict):
        raise LichessParseError("puzzle payload was not a JSON object")
    game = payload.get("game")
    puzzle = payload.get("puzzle")
    if not isinstance(game, dict) or not isinstance(puzzle, dict):
        raise LichessParseError("puzzle payload missing game/puzzle objects")

    pid = puzzle.get("id")
    pgn = game.get("pgn")
    solution = puzzle.get("solution")
    initial_ply = puzzle.get("initialPly")
    if not isinstance(pid, str) or not pid.strip():
        raise LichessParseError("puzzle payload missing id")
    if not isinstance(pgn, str) or not pgn.strip():
        raise LichessParseError("puzzle payload missing pgn")
    if len(pgn) > _MAX_PUZZLE_PGN_CHARS:
        raise LichessParseError("puzzle pgn exceeds cap")
    if not isinstance(solution, list) or not solution:
        raise LichessParseError("puzzle payload missing solution")
    if len(solution) > _MAX_PUZZLE_SOLUTION_MOVES:
        raise LichessParseError("puzzle solution exceeds cap")
    sol0 = solution[0]
    if not isinstance(sol0, str) or not sol0:
        raise LichessParseError("puzzle solution[0] not a string")
    # ``bool`` is an ``int`` subclass; exclude it explicitly so a JSON
    # ``true`` can't masquerade as a ply count.
    if not isinstance(initial_ply, int) or isinstance(initial_ply, bool) or initial_ply < 0:
        raise LichessParseError("puzzle payload missing initialPly")

    rating_raw = puzzle.get("rating")
    rating = rating_raw if isinstance(rating_raw, int) and not isinstance(rating_raw, bool) else 0
    themes_raw = puzzle.get("themes")
    themes = (
        tuple(t for t in themes_raw if isinstance(t, str)) if isinstance(themes_raw, list) else ()
    )

    sans = pgn.split()
    replay_count = min(len(sans), initial_ply + 1)
    board = chess.Board()
    for san in sans[:replay_count]:
        try:
            board.push_san(san)
        except ValueError as exc:  # covers Illegal/Invalid/Ambiguous move errors
            raise LichessParseError(f"puzzle pgn replay failed: {exc}") from exc

    try:
        move = chess.Move.from_uci(sol0)
    except ValueError as exc:
        raise LichessParseError(f"puzzle solution move not UCI: {exc}") from exc
    if move not in board.legal_moves:
        raise LichessParseError("puzzle solution[0] not legal in derived position")

    return LichessPuzzle(
        id=pid.strip(),
        rating=rating,
        themes=themes,
        solver_fen=board.fen(),
        solver_move_uci=sol0,
        side=board.turn,
    )


def fetch_puzzle_by_theme(angle_slug: str, *, difficulty: str | None = None) -> LichessPuzzle:
    """Fetch one theme-matched puzzle via ``GET /api/puzzle/next``.

    ``angle_slug`` MUST be in ``_PUZZLE_ANGLE_ALLOWED`` and ``difficulty``
    (when given) in ``_PUZZLE_DIFFICULTY_ALLOWED`` — both validated before they
    touch the URL (SSRF / injection guard, same discipline as
    ``_validated_username``).  A non-conforming value is a programming error
    and raises ``ValueError``.

    Returns a fully-derived, legality-checked :class:`LichessPuzzle`.  Raises
    the client's typed errors (``LichessRateLimited`` / ``LichessUpstreamError``
    / ``LichessParseError``); a structurally malformed body or an illegal
    ``solution[0]`` surfaces as ``LichessParseError`` (fail closed) so the
    caller can fall back to the local corpus.
    """
    if not isinstance(angle_slug, str) or angle_slug not in _PUZZLE_ANGLE_ALLOWED:
        raise ValueError(f"unsupported puzzle angle: {angle_slug!r}")
    if difficulty is not None and difficulty not in _PUZZLE_DIFFICULTY_ALLOWED:
        raise ValueError(f"unsupported puzzle difficulty: {difficulty!r}")

    params: dict[str, str] = {"angle": angle_slug}
    if difficulty is not None:
        params["difficulty"] = difficulty

    response = _request_json_bounded(
        "GET",
        f"{LICHESS_API_BASE}/api/puzzle/next",
        headers=_headers(),
        params=params,
        timeout=_PUZZLE_TIMEOUT,
        max_bytes=_MAX_PUZZLE_BODY_BYTES,
        context="puzzle fetch",
    )

    if response.status_code == 429:
        raise LichessRateLimited(
            "puzzle fetch: rate limited", retry_after=_retry_after_seconds(response.headers)
        )
    if response.status_code >= 500:
        raise LichessUpstreamError(f"puzzle fetch: upstream {response.status_code}")
    if response.status_code >= 400:
        raise LichessUpstreamError(f"puzzle fetch: unexpected status {response.status_code}")

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LichessParseError(f"puzzle body was not JSON: {exc}") from exc
    return _parse_puzzle_payload(payload)
