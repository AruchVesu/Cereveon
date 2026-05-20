"""HTTP surface for the per-mistake study-plan agent.

One endpoint, authenticated:

* ``GET /coach/plan/today`` — return the player's most recent active
  study plan + the puzzle currently due (the lowest-day-offset puzzle
  whose ``due_at <= now()`` and ``completed_at IS NULL``).

The Android Home-screen ``TodaysDrillCard`` (phase 4) polls this on
home-open.  When the response carries a puzzle, the card surfaces the
LLM coach verdict (phase 2+) + a "Start drill" button that launches
the existing ``MistakeReplayBottomSheet`` against the puzzle's FEN.
When the response is ``null``, the card stays hidden.

Trust posture
-------------
Read-only.  The endpoint serves the persisted plan; it does NOT
regenerate or LLM-call.  The plan was written by the background-task
``generate_plan_async`` invoked from /game/finish.  XP credit on
puzzle completion goes through the existing
``POST /training/verify-replay`` + ``POST /training/solve`` path
(phase 4 wires the Android side; phase 1 has no completion path
because the UI doesn't exist yet).
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.coach.study_plan.models import (
    PLAN_DAY_OFFSETS,
    STATUS_ACTIVE,
    MistakeStudyPlan,
    MistakeStudyPuzzle,
)
from llm.seca.shared_limiter import limiter

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/coach", tags=["coach"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class TodayPuzzleResponse(BaseModel):
    """The puzzle currently surfaced in the active plan.

    ``day_offset`` is one of ``PLAN_DAY_OFFSETS`` (0, 3, 7) — the
    client can render "Day 1 / 3", "Day 2 / 3" etc. via a static map.
    """

    day_offset: int
    fen: str
    expected_move_uci: str
    source_type: str
    due_at: str
    """ISO-8601 UTC timestamp.  ``due_at <= now()`` is invariant when
    this object is non-null (the endpoint only returns puzzles that
    are actually due)."""


class TodayPlanResponse(BaseModel):
    """The top-level shape of ``GET /coach/plan/today`` when the
    player has an active plan.

    The Android client decodes either this object or a JSON ``null``
    (no active plan) — phase 4 hides the Home-screen card in the null
    case.
    """

    plan_id: str
    theme: str
    """Phase 2+ populated; phase 1 always ``"generic"``."""

    verdict: str
    """LLM-written retrospective on the originating mistake.  Phase 1
    always ``""`` (empty string); phase 2 populates it with a <= 100-
    word Mode-2-validator-clean string."""

    total_days: int
    """How many puzzles are in the plan (always ``len(PLAN_DAY_OFFSETS) == 3``
    in phase 1, but surfaced as a field so the UI can render
    "Day N of M" without hard-coding the upper bound)."""

    today_puzzle: TodayPuzzleResponse | None
    """The puzzle currently due, or ``null`` when the plan is active
    but no puzzle's ``due_at`` has elapsed yet (e.g. day-0 is solved,
    day-3 isn't due for another 2 days)."""


# ---------------------------------------------------------------------------
# GET /coach/plan/today
# ---------------------------------------------------------------------------


@router.get("/plan/today", response_model=TodayPlanResponse | None)
@limiter.limit("60/minute")
def get_today_plan(
    request: Request,
    player: Player = Depends(get_current_player),
    db: DBSession = Depends(get_db),
) -> TodayPlanResponse | None:
    """Return the player's active study plan + today's due puzzle.

    Selection
    ---------
    * Most recent ``MistakeStudyPlan`` for the authenticated player
      with ``status == STATUS_ACTIVE``, ordered by ``created_at DESC``.
    * The puzzle whose ``due_at <= now()`` AND ``completed_at IS NULL``,
      with the lowest ``day_offset`` (so day-0 surfaces before day-3,
      day-3 before day-7).

    Response
    --------
    * ``200`` with the ``TodayPlanResponse`` shape when an active
      plan exists.
    * ``200`` with body ``null`` when no active plan exists (no
      qualifying game has landed yet, or every plan is completed).
    * ``200`` with ``today_puzzle: null`` when an active plan exists
      but no puzzle is currently due.

    No 4xx beyond the auth path (``get_current_player`` returns 401
    on missing/invalid token).
    """
    plan = (
        db.query(MistakeStudyPlan)
        .filter(
            MistakeStudyPlan.player_id == player.id,
            MistakeStudyPlan.status == STATUS_ACTIVE,
        )
        .order_by(MistakeStudyPlan.created_at.desc())
        .first()
    )
    if plan is None:
        return None

    now = datetime.utcnow()
    due_puzzle = (
        db.query(MistakeStudyPuzzle)
        .filter(
            MistakeStudyPuzzle.plan_id == plan.id,
            MistakeStudyPuzzle.due_at <= now,
            MistakeStudyPuzzle.completed_at.is_(None),
        )
        .order_by(MistakeStudyPuzzle.day_offset.asc())
        .first()
    )

    today_puzzle_field: TodayPuzzleResponse | None = None
    if due_puzzle is not None:
        today_puzzle_field = TodayPuzzleResponse(
            day_offset=due_puzzle.day_offset,
            fen=due_puzzle.fen,
            expected_move_uci=due_puzzle.expected_move_uci,
            source_type=due_puzzle.source_type,
            due_at=due_puzzle.due_at.isoformat(),
        )

    return TodayPlanResponse(
        plan_id=plan.id,
        theme=plan.theme,
        verdict=plan.verdict,
        total_days=len(PLAN_DAY_OFFSETS),
        today_puzzle=today_puzzle_field,
    )
