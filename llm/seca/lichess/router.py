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
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.router import get_current_player, get_db
from llm.seca.lichess import client as lichess_client
from llm.seca.lichess import import_service
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lichess", tags=["lichess"])

# Lichess username spec: 2–30 chars, alphanumerics plus ``_`` and ``-``.
# Source: lichess.org/api docs — same rule the signup form enforces.
# We pre-validate so we never send a structurally-invalid handle to
# Lichess (which would return a vague 400) and so log injection via the
# username string is impossible.
_LICHESS_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,30}$")

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
    username: str

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        v = (v or "").strip()
        if not _LICHESS_USERNAME_RE.fullmatch(v):
            raise ValueError("username must be 2-30 chars of letters, digits, '_' or '-'")
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
    """Attach a Lichess handle and (on first link) calibrate rating."""
    try:
        return import_service.link_account(db, player, req.username)
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

    Incremental: uses ``LinkedAccount.last_imported_at`` as the
    ``since`` watermark so repeated calls walk forward through the
    user's history without re-fetching games already imported.
    """
    try:
        return import_service.import_user_games(
            db,
            player,
            max_games=max_games,
            rated=rated,
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise _translate_client_error(exc) from exc
