"""HTTP surface for user reports of AI-generated coach content.

One endpoint, authenticated:

* ``POST /coach/report`` — persist one report raised from the in-app
  "Report" affordance on a coach message.

Required by Google Play's AI-Generated Content policy: an in-app path to
flag offensive AI output without leaving the app, whose reports inform
moderation.  The reported text is untrusted user-flagged content; it is
length-capped + whitespace-trimmed at the Pydantic boundary, stored
verbatim in ``content_reports``, and NEVER logged (the log line carries
only the server-issued player id, the surface, and the content length,
so there is no log-injection surface and no content duplicated into the
log stream).  Rows are operator-read only and never surface back into
any coaching / prompt / adaptation path.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.
# pylint: disable=unused-argument

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.moderation.models import (
    MAX_REPORT_CONTENT_LEN,
    MAX_REPORT_FEN_LEN,
    MAX_REPORT_REASON_LEN,
    REPORT_SURFACES,
    ContentReport,
)
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["moderation"])


class ReportRequest(BaseModel):
    content: str
    surface: str
    fen: str | None = None
    reason: str | None = None

    @field_validator("content")
    @classmethod
    def _validate_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("content must not be empty")
        if len(v) > MAX_REPORT_CONTENT_LEN:
            raise ValueError(f"content must be at most {MAX_REPORT_CONTENT_LEN} chars")
        return v

    @field_validator("surface")
    @classmethod
    def _validate_surface(cls, v: str) -> str:
        v = v.strip()
        if v not in REPORT_SURFACES:
            raise ValueError(f"surface must be one of {sorted(REPORT_SURFACES)}")
        return v

    @field_validator("fen")
    @classmethod
    def _validate_fen(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > MAX_REPORT_FEN_LEN:
            raise ValueError(f"fen must be at most {MAX_REPORT_FEN_LEN} chars")
        return v

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > MAX_REPORT_REASON_LEN:
            raise ValueError(f"reason must be at most {MAX_REPORT_REASON_LEN} chars")
        return v


class ReportResponse(BaseModel):
    # Fixed literal so a client can assert on the body without the row id.
    status: str
    # Server-issued row id, so a support conversation can reference it.
    id: str


@router.post("/coach/report", response_model=ReportResponse)
@limiter.limit("10/minute")
def report_content(
    req: ReportRequest,
    request: Request,
    player: Player = Depends(get_current_player),
    db: DBSession = Depends(get_db),
) -> ReportResponse:
    """Persist one report of AI-generated coach content.

    Fire-and-forget from the client's perspective: no dedup (reporting
    the same message twice is two rows) and no synchronous moderation —
    rows land in an operator-reviewed queue (``reviewed = 0``).
    """
    row = ContentReport(
        player_id=player.id,
        content=req.content,
        surface=req.surface,
        fen=req.fen,
        reason=req.reason,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # No reported content in the log line: player.id is a server-issued
    # key, surface is a validated closed-vocabulary value, and len() is an
    # int — nothing attacker-controlled reaches the log stream (CWE-117).
    logger.info(
        "content report received: player_id=%s surface=%s chars=%d",
        player.id,
        req.surface,
        len(req.content),
    )
    return ReportResponse(status="received", id=row.id)
