"""HTTP surface for user-submitted product feedback.

One endpoint, authenticated:

* ``POST /feedback`` — persist one free-form feedback message from the
  authenticated player.

The message body is untrusted user text.  It is length-capped and
whitespace-trimmed at the Pydantic boundary, stored verbatim in the
``feedback_messages`` table, and NEVER logged — the log line carries
only the server-issued player id and the message length, so there is no
log-injection surface (CWE-117) and no PII duplication into the log
stream.  The text is also never read back into any coaching, prompt, or
adaptation path (see the package docstring).
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  Pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.feedback.models import (
    MAX_APP_VERSION_LEN,
    MAX_FEEDBACK_MESSAGE_LEN,
    FeedbackMessage,
)
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    message: str
    # Optional client-reported app version (Android sends
    # ``BuildConfig.VERSION_NAME``); older clients may omit it.
    app_version: str | None = None

    @field_validator("message")
    @classmethod
    def _validate_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        if len(v) > MAX_FEEDBACK_MESSAGE_LEN:
            raise ValueError(f"message must be at most {MAX_FEEDBACK_MESSAGE_LEN} chars")
        return v

    @field_validator("app_version")
    @classmethod
    def _validate_app_version(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            # An all-whitespace version string carries no information;
            # normalise to NULL so queries don't have to special-case "".
            return None
        if len(v) > MAX_APP_VERSION_LEN:
            raise ValueError(f"app_version must be at most {MAX_APP_VERSION_LEN} chars")
        return v


class FeedbackResponse(BaseModel):
    # Fixed literal "received" — lets a client assert on the body
    # without depending on the row id, mirroring /game/coach-feedback's
    # ``{"status": "recorded"}`` shape.
    status: str

    # Server-issued row id, returned so support conversations can
    # reference a concrete submission ("feedback <id>").
    id: str


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------


@router.post("/feedback", response_model=FeedbackResponse)
@limiter.limit("5/minute")
def submit_feedback(
    req: FeedbackRequest,
    request: Request,
    player: Player = Depends(get_current_player),
    db: DBSession = Depends(get_db),
) -> FeedbackResponse:
    """Persist one feedback message for the authenticated player.

    Fire-and-forget from the client's perspective: no dedup (sending
    the same text twice is two rows — feedback is not idempotent by
    nature) and no moderation pass (rows are operator-read only and
    never surface back into the product).
    """
    row = FeedbackMessage(
        player_id=player.id,
        message=req.message,
        app_version=req.app_version,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Deliberately no message content in the log line: player.id is a
    # server-issued primary key and len() is an int, so nothing
    # attacker-controlled reaches the log stream (CWE-117).
    logger.info(
        "feedback received: player_id=%s chars=%d",
        player.id,
        len(req.message),
    )
    return FeedbackResponse(status="received", id=row.id)
