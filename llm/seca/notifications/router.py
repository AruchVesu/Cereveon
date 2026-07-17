"""HTTP surface for the in-app notification feed.

Four endpoints, all authenticated and owner-scoped
(docs/API_CONTRACTS.md §40):

* ``GET  /notifications``                    — feed + badge count.
* ``POST /notifications/{id}/read``          — mark one row read.
* ``POST /notifications/read-all``           — mark every visible row read.
* ``POST /notifications/{id}/dismiss``       — soft-delete one row.

Rows are only ever created server-side (import worker, disconnect
detection) — there is deliberately no POST-to-create surface, so a
hostile client cannot mint feed entries.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it — same
# file-wide rationale as the lichess router.
# pylint: disable=unused-argument

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.router import get_current_player, get_db
from llm.seca.notifications import service
from llm.seca.notifications.models import Notification
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# uuid4 = 36 chars; 64 is the shared defensive cap (same as the review
# and events routers) so a hostile path segment can't waste a DB
# round-trip on a megabyte string.
_NOTIFICATION_ID_MAX_LEN = 64


def _owned_notification_or_error(db: DBSession, notification_id: str, player) -> Notification:
    if len(notification_id) > _NOTIFICATION_ID_MAX_LEN:
        raise HTTPException(status_code=400, detail="notification_id too long")
    row = db.query(Notification).filter(Notification.id == notification_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    if row.player_id != str(player.id):
        # 403 (not 404) so cross-player probes stay visible in access
        # logs — same convention as the review router.
        raise HTTPException(
            status_code=403,
            detail="Cannot access another player's notification",
        )
    return row


@router.get("")
def feed(
    request: Request,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Visible notifications (newest first) + the bell-badge count."""
    return service.get_feed(db, str(player.id))


@router.post("/read-all")
@limiter.limit("30/minute")
def read_all(
    request: Request,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Mark every visible notification read; rows stay in the feed."""
    marked = service.mark_all_read(db, str(player.id))
    return {"marked": marked, "unread_count": 0}


@router.post("/{notification_id}/read")
@limiter.limit("30/minute")
def read_one(
    request: Request,
    notification_id: str,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Mark one notification read (idempotent)."""
    row = _owned_notification_or_error(db, notification_id, player)
    service.mark_read(db, row)
    return {"read": True, "unread_count": service.unread_badge_count(db, str(player.id))}


@router.post("/{notification_id}/dismiss")
@limiter.limit("30/minute")
def dismiss_one(
    request: Request,
    notification_id: str,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Soft-delete one notification from the feed (idempotent)."""
    row = _owned_notification_or_error(db, notification_id, player)
    service.dismiss(db, row)
    return {"dismissed": True, "unread_count": service.unread_badge_count(db, str(player.id))}
