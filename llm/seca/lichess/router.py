"""HTTP surface for the Lichess adapter.

Four endpoints, all authenticated:

* ``POST   /lichess/link``    — attach a Lichess handle to the player.
* ``DELETE /lichess/link``    — detach it.
* ``GET    /lichess/status``  — link state + import counters.
* ``POST   /lichess/import``  — pull the next slice of games (≤ 100).

The import endpoint is intentionally synchronous for MVP — the backend
has no background-job framework yet (see ``project_two_tier_deployment``)
and an async fire-and-forget would tie progress visibility to a single
worker process.  A 100-game cap keeps the worst-case latency bounded.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session as DBSession

# Private import is deliberate — ``_is_sqlite`` is the single source of
# truth for dialect detection and we want this module to size its worker
# pool based on the same value the schema-init pattern uses.  An
# additional public wrapper would just be drift bait.
from llm.seca.auth.router import _is_sqlite as _DB_IS_SQLITE
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.lichess import client as lichess_client
from llm.seca.lichess import import_service
from llm.seca.lichess.models import LichessImportJob
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lichess", tags=["lichess"])

# Thread-pool worker for v2 async imports.
#
# Size:
#   * SQLite (dev): 1 worker.  SQLite serialises writers; with 4 workers
#     each running per-game commits, the auth session-rotation insert
#     in the next request thread sporadically hits ``database is
#     locked`` after ``busy_timeout``.  One worker keeps the dev story
#     loud-and-clear (a queued import blocks others) without spurious
#     test failures.
#   * Postgres (prod): 4 workers.  Concurrent imports from different
#     players run in parallel; the limit caps total Lichess upstream
#     fan-out and protects the application worker count.
#
# Shutdown is handled by ``llm/server.py`` lifespan: it calls
# ``_executor.shutdown(wait=False, cancel_futures=True)`` so SIGTERM
# doesn't hang on the long-running ``httpx.iter_lines`` reads.  Any
# job left in ``running`` state at boot is swept to ``failed`` by
# ``import_service.cleanup_stale_import_jobs_on_startup``.
_executor = ThreadPoolExecutor(
    max_workers=1 if _DB_IS_SQLITE else 4,
    thread_name_prefix="lichess-import",
)


def engine_pool_from_request(request: Request):
    """App-state engine pool for background-worker submission, fail-soft.

    ``request.app`` raises ``KeyError`` on the minimal handler-direct
    requests the test suite constructs (no ``app`` in the ASGI scope),
    so this reads via ``scope.get`` instead.  ``None`` merely disables
    the post-import analysis pass; the import itself is unaffected.

    Public (no underscore): consumed by this router's import dispatch
    AND by the /auth/lichess post-sign-in kick in llm.seca.auth.router.
    """
    app = request.scope.get("app")
    state = getattr(app, "state", None)
    return getattr(state, "engine_pool", None)


# Hard upper bound on a single import slice.  Backend has no background
# job framework; a synchronous request must complete within a sane
# proxy timeout.  100 typical games is ~30-60s of Lichess streaming on
# the public quota; bigger backfills are reached by repeated calls
# (the watermark on LinkedAccount makes subsequent calls cheap).
_MAX_IMPORT_PER_CALL = 100


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LinkRequest(BaseModel):
    """POST /lichess/link — OAuth authorization-code linking.

    A logged-in player proves ownership of the Lichess account through
    the same PKCE authorization flow as sign-in (a DEDICATED redirect,
    ``ai.chesscoach.app://lichess-link``), and the server links the
    VERIFIED identity.  This replaces the old self-asserted-``username``
    link, which let a user attach any handle they did not own.  Same
    server-side exchange as ``POST /auth/lichess`` — the code + verifier
    are forwarded here, never a raw Lichess token.
    """

    code: str
    code_verifier: str

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not lichess_client.AUTH_CODE_RE.fullmatch(v):
            raise ValueError("malformed authorization code")
        return v

    @field_validator("code_verifier")
    @classmethod
    def _validate_code_verifier(cls, v: str) -> str:
        if not lichess_client.CODE_VERIFIER_RE.fullmatch(v):
            raise ValueError("malformed code_verifier (RFC 7636 §4.1 shape required)")
        return v


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def _translate_client_error(exc: Exception) -> HTTPException:
    """Map client / service exceptions to HTTPException with stable codes."""
    if isinstance(exc, lichess_client.LichessUserNotFound):
        return HTTPException(status_code=404, detail="Lichess user not found")
    if isinstance(exc, import_service.LichessAlreadyLinkedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, import_service.LichessNotLinkedError):
        return HTTPException(status_code=400, detail="No Lichess account linked")
    if isinstance(exc, lichess_client.LichessRateLimited):
        headers: dict[str, str] = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        return HTTPException(
            status_code=503,
            detail="Lichess is rate-limiting requests; retry shortly",
            headers=headers or None,
        )
    if isinstance(exc, (lichess_client.LichessUpstreamError, lichess_client.LichessParseError)):
        logger.warning("Lichess upstream error: %s", exc)
        return HTTPException(status_code=502, detail="Lichess upstream error")
    if isinstance(exc, import_service.LichessImportError):
        return HTTPException(status_code=400, detail=str(exc))
    # Anything else genuinely is a server bug — let FastAPI's default
    # 500 handler render it after this function re-raises.  Returning
    # an HTTPException here would mask the traceback.
    raise exc  # pragma: no cover — re-raised for visibility


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/link")
@limiter.limit("10/minute")
def link(
    request: Request,
    req: LinkRequest,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Link a Lichess account to the authenticated player via OAuth.

    Ownership is PROVEN by the authorization-code exchange (not a typed
    username), so the verified identity is linked and — mirroring OAuth
    sign-in — CLAIMS the handle from any other account's self-asserted
    link (``claim_from_other_player=True``).  First link also calibrates
    rating.

    The exchange uses the dedicated LINK redirect_uri and revokes the
    Lichess token immediately after the identity is read — the same
    server-side, token-never-on-device pattern as ``POST /auth/lichess``.
    """
    try:
        lichess_token = lichess_client.exchange_authorization_code(
            req.code,
            req.code_verifier,
            redirect_uri=lichess_client.LICHESS_OAUTH_LINK_REDIRECT_URI,
        )
        try:
            account = lichess_client.fetch_account(lichess_token)
        except lichess_client.LichessClientError:
            # Exchange succeeded → a live token exists at Lichess; revoke
            # it before propagating the fetch failure.  Best-effort.
            lichess_client.revoke_token(lichess_token)
            raise
    except lichess_client.LichessOAuthError:
        raise HTTPException(status_code=401, detail="Lichess authorization failed")
    except lichess_client.LichessRateLimited:
        raise HTTPException(status_code=503, detail="Lichess is busy; try again shortly")
    except lichess_client.LichessClientError:
        raise HTTPException(status_code=502, detail="Lichess upstream error")

    lichess_client.revoke_token(lichess_token)

    # fetch_account validated the id shape (fail-closed); a trusted key now.
    try:
        return import_service.link_account(
            db,
            player,
            str(account["id"]),
            profile=account,
            claim_from_other_player=True,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise _translate_client_error(exc) from exc


@router.delete("/link")
@limiter.limit("10/minute")
def unlink(
    request: Request,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Detach the player's Lichess link.  Imported games are retained."""
    removed = import_service.unlink_account(db, player)
    return {"unlinked": removed}


@router.get("/status")
def status(
    request: Request,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Return link state + count of imported games."""
    return import_service.get_status(db, player)


@router.post("/import")
@limiter.limit("6/minute")
def trigger_import(
    request: Request,
    response: Response,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
    max_games: int = Query(
        default=50,
        ge=1,
        le=_MAX_IMPORT_PER_CALL,
        description=(
            "Max games to fetch in this call.  Cap is "
            f"{_MAX_IMPORT_PER_CALL} per request; repeat to backfill more."
        ),
    ),
    rated: bool = Query(default=True, description="Filter to rated games only."),
):
    """Pull the next slice of games for the linked player.

    Branches on ``X-API-Version``:

    * ``1`` (default, omitted) — synchronous: blocks until the slice
      completes, returns 200 + the legacy summary dict.  The shipped
      Android v1 client expects this shape.
    * ``2`` — asynchronous: creates a ``LichessImportJob`` row,
      dispatches the worker to ``_executor``, returns 202 + the job
      payload.  The Android v2 client polls ``GET /lichess/import/job/
      {job_id}`` every 2s and renders a determinate progress bar.

    Incremental in both modes: uses ``LinkedAccount.last_imported_at``
    as the ``since`` watermark so repeated calls walk forward through
    the user's history without re-fetching games already imported.

    Rate limit (``6/minute``) defends Lichess upstream cost in v1 and
    job-spawn cost in v2 (per-player coalescing additionally prevents
    a tap-storm from creating duplicate jobs).
    """
    api_version = request.headers.get("X-API-Version", "1")

    try:
        if api_version == "1":
            return import_service.import_user_games(
                db,
                player,
                max_games=max_games,
                rated=rated,
            )

        # v2 async path.  Worker submission happens via the dispatch
        # callback — invoked by start_import_job, inside the per-player
        # lock, for FRESHLY-CREATED jobs only.  Keying the submit on
        # ``status == 'queued'`` out here (the pre-2026-07-03 pattern)
        # double-submits when a job created by an earlier call is
        # coalesced back while still awaiting executor pickup.
        job = import_service.start_import_job(
            db,
            player,
            max_games=max_games,
            dispatch=lambda job_id: _executor.submit(
                import_service.run_import_job,
                job_id,
                max_games=max_games,
                rated=rated,
                # Captured from app.state at submit time — the worker has
                # no request context (same pattern as /game/finish's
                # accuracy recompute).  None (pool-less boot: tests,
                # degraded deploys) skips the post-import analysis pass;
                # the import itself is unaffected.
                engine_pool=engine_pool_from_request(request),
            ),
        )
        response.status_code = 202
        return import_service.serialize_job(job)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise _translate_client_error(exc) from exc


@router.get("/import/job/{job_id}")
@limiter.limit("120/minute")
def get_import_job(
    request: Request,
    job_id: str,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Return the current state of a Lichess import job.

    Owner-scoped: returns 404 if the job does not exist OR if it
    belongs to another player.  We do not differentiate the two cases
    to avoid leaking the existence of other players' jobs.

    Rate limit (``120/minute``) leaves headroom for the Android
    client's 2s poll cadence (30/min steady state) plus retries.
    """
    job = db.get(LichessImportJob, job_id)
    if job is None or job.player_id != player.id:
        raise HTTPException(status_code=404, detail="Import job not found")
    return import_service.serialize_job(job)
