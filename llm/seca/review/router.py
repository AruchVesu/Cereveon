"""HTTP surface for the post-game review.

Two endpoints, both authenticated and owner-scoped:

* ``POST /game/{event_id}/review`` — start (or coalesce/retry) the
  review job.  202 when worker work was queued, 200 when an existing
  row already answers.
* ``GET  /game/{event_id}/review`` — poll the row.  The Android client
  polls every 2s while ``status`` is non-terminal and renders whatever
  stages have landed (the three-wave progressive UX).

The job pattern (row + thread-pool worker + polling GET) deliberately
mirrors the Lichess import endpoints — the backend has no background-
job framework, and this shape is already proven against process
restarts (startup janitor) and SIGTERM (lifespan executor shutdown,
wired in ``llm/server.py``).
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it — same
# file-wide rationale as the lichess router.
# pylint: disable=unused-argument

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.router import _is_sqlite as _DB_IS_SQLITE
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.events.models import GameEvent

# Reuse the lichess router's fail-soft app-state reader — same worker
# submission pattern, same "None disables the engine stage" semantics.
from llm.seca.lichess.router import engine_pool_from_request
from llm.seca.review import service as review_service
from llm.seca.review.models import ANALYSIS_VERSION, GameReview, REVIEW_STATUS_ACTIVE
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/game", tags=["review"])

# Mirrors events.router._EVENT_ID_MAX_LEN (uuid4 = 36 chars; 64 is the
# shared defensive cap) so a hostile path segment can't waste a DB
# round-trip on a megabyte string.
_EVENT_ID_MAX_LEN = 64

# Review jobs are heavier than import jobs per unit (an engine pass +
# LLM calls, ~20-40s end to end) but bounded per player by the POST
# rate limit and per row by the unique constraint.  SQLite (dev) gets a
# single worker for the same writer-serialisation reason the import
# executor documents; Postgres gets 2 — enough to overlap one engine
# stage with one LLM stage without letting reviews starve the pool the
# /live/move path shares.
_executor = ThreadPoolExecutor(
    max_workers=1 if _DB_IS_SQLITE else 2,
    thread_name_prefix="game-review",
)


def shutdown_executor() -> None:
    """SIGTERM path — called from llm/server.py lifespan teardown."""
    _executor.shutdown(wait=False, cancel_futures=True)


def _owned_event_or_error(db: DBSession, event_id: str, player) -> GameEvent:
    if len(event_id) > _EVENT_ID_MAX_LEN:
        raise HTTPException(status_code=400, detail="event_id too long")
    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    if event.player_id != str(player.id):
        # 403 (not 404) so cross-player probes stay visible in access
        # logs — same convention as the game-positions endpoint.
        raise HTTPException(
            status_code=403,
            detail="Cannot access game review for another player",
        )
    return event


@router.post("/{event_id}/review")
@limiter.limit("6/minute")
def start_review(
    request: Request,
    response: Response,
    event_id: str,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Start, coalesce, or retry the review for one imported game.

    Semantics by existing-row state:

    * none            — create + dispatch (202)
    * queued/running/engine_done — coalesce, no new work (202: the job
      the client should poll is still in flight)
    * failed          — requeue the same row (202)
    * complete, full LLM outcome — serve as-is (200, no dispatch)
    * complete, fallback/skipped LLM outcome — re-run Wave 3 only
      ("Try coach review again"), unless the entitlement cap is still
      blocking, in which case the row is served unchanged (200)

    Never 402s: the engine review (Wave 2) is free on every imported
    game; the entitlement gates only the LLM stage, and a blocked stage
    lands as ``llm.outcome == "skipped_entitlement"`` with the quota
    snapshot in ``entitlement`` for the client's CTA copy.

    Errors: 400 (ineligible: not a Lichess import / too short, with a
    machine-readable ``code``), 403/404 (ownership), 429 (rate limit).
    """
    event = _owned_event_or_error(db, event_id, player)

    try:
        review, dispatched = review_service.start_review(
            db,
            player,
            event,
            dispatch=lambda review_id: _executor.submit(
                review_service.run_review_job,
                review_id,
                # Captured from app.state at submit time — the worker
                # has no request context.  None (pool-less boot: tests,
                # degraded deploys) fails the engine stage loudly rather
                # than hanging the job.
                engine_pool=engine_pool_from_request(request),
            ),
        )
    except review_service.ReviewEligibilityError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    response.status_code = 202 if dispatched or review.status in REVIEW_STATUS_ACTIVE else 200
    return review_service.serialize_review(
        review,
        entitlement=review_service.entitlement_summary(db, player),
    )


@router.get("/{event_id}/review")
@limiter.limit("120/minute")
def get_review(
    request: Request,
    event_id: str,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Poll the review for one game.

    404 when no review row exists at the current analysis version (the
    client shows the "Get coach review" button); 200 with the serialized
    row otherwise.  Rate limit leaves headroom for the 2s poll cadence
    (30/min steady state) plus retries — same budget as the import-job
    poll.
    """
    event = _owned_event_or_error(db, event_id, player)
    review = (
        db.query(GameReview)
        .filter(
            GameReview.game_event_id == event.id,
            GameReview.analysis_version == ANALYSIS_VERSION,
        )
        .first()
    )
    if review is None:
        raise HTTPException(status_code=404, detail="no review for this game yet")
    return review_service.serialize_review(
        review,
        entitlement=review_service.entitlement_summary(db, player),
    )
