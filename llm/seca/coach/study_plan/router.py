"""HTTP surface for the per-mistake study-plan agent.

Two endpoints, both authenticated:

* ``GET /coach/plan/today`` — return the player's most recent active
  study plan, the puzzle currently due (the lowest-day-offset puzzle
  whose ``due_at <= now()`` and ``completed_at IS NULL``), and the
  full week schedule (``days[]``) for the overview screen.

* ``POST /coach/plan/puzzle/complete`` — mark one day's puzzle solved
  and advance the plan; flips it to ``completed`` once every day is
  done.  This closes the loop the phase-1 scaffold left open (nothing
  used to write ``MistakeStudyPuzzle.completed_at``, so day 0 re-served
  forever and plans never finished).

The Home-screen ``TodaysDrillCard`` polls ``GET`` on home-open.  When
the response carries a puzzle, the card surfaces the LLM coach verdict
(phase 2+) + a "Start drill" button that launches the existing
``MistakeReplayBottomSheet`` against the puzzle's FEN.  The
week-overview screen renders ``days[]``.  When the response is
``null``, the card stays hidden.

Trust posture
-------------
``GET`` is read-only; it serves the persisted plan and does NOT
regenerate or LLM-call.  ``complete`` records plan PROGRESS only — the
engine-truth gate already happened on the
``POST /training/verify-replay`` → ``POST /training/solve`` path the
client runs first.  Marking progress carries no cross-user value, so
it trusts the caller's assertion (same posture as ``/training/solve``)
and is idempotent + ownership-scoped to the authenticated player.  XP
still flows through ``/training/solve``; this endpoint only advances
the study-plan schedule.
"""

# Slowapi reads ``request: Request`` from each rate-limited handler's
# signature even when the handler body doesn't reference it.  pylint
# flags every such parameter as unused; disabling the rule file-wide
# rather than per-handler keeps the diff stable as new endpoints land.
# pylint: disable=unused-argument

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.auth.router import get_current_player, get_db
from llm.seca.coach.study_plan.models import (
    PLAN_DAY_OFFSETS,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
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


class PlanDayResponse(BaseModel):
    """One day-slot in the week-overview list.

    The week-overview screen renders the whole plan at a glance:
    each day is ``completed`` (done), ``is_due`` (available to start
    now), or neither (still locked behind its ``due_at``).  Unlike
    ``today_puzzle`` this carries no FEN / expected move — the overview
    only needs the schedule + status; the playable position comes from
    ``today_puzzle`` (or a follow-up ``GET`` once the next day unlocks).
    """

    day_offset: int
    due_at: str
    """ISO-8601 UTC timestamp of when this day's puzzle unlocks."""

    completed: bool
    """True once the day's puzzle has been solved (``completed_at`` set)."""

    is_due: bool
    """True when the puzzle is available now — ``due_at <= now()`` AND
    not yet completed.  The natural ``today_puzzle`` is the lowest
    ``day_offset`` day with ``is_due == True``."""

    source_type: str
    """``"original"`` (the player's actual mistake) or ``"library"``
    (a theme-matched practice puzzle)."""


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

    status: str
    """One of ``STATUSES`` — ``"active"`` while the week is in
    progress, ``"completed"`` once all days are solved.  ``GET`` only
    ever returns ``"active"`` plans; the completion endpoint returns
    the freshly-``"completed"`` plan so the client can show the
    week-complete celebration."""

    total_days: int
    """How many puzzles are in the plan (always ``len(PLAN_DAY_OFFSETS) == 3``
    in phase 1, but surfaced as a field so the UI can render
    "Day N of M" without hard-coding the upper bound)."""

    today_puzzle: TodayPuzzleResponse | None
    """The puzzle currently due, or ``null`` when the plan is active
    but no puzzle's ``due_at`` has elapsed yet (e.g. day-0 is solved,
    day-3 isn't due for another 2 days)."""

    days: list[PlanDayResponse]
    """The full week schedule, ordered by ``day_offset``.  Powers the
    week-overview screen (which day is done / available / locked).
    Always ``len(PLAN_DAY_OFFSETS)`` entries."""


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _serialize_plan(plan: MistakeStudyPlan, now: datetime) -> TodayPlanResponse:
    """Build the wire response from a plan + its puzzles.

    Shared by ``GET /coach/plan/today`` and
    ``POST /coach/plan/puzzle/complete`` so both surfaces compute the
    ``today_puzzle`` / ``days`` view identically.  Reads only the
    in-session ``plan.puzzles`` collection (no extra query) — the
    caller has already loaded the plan.

    ``today_puzzle`` is the lowest-``day_offset`` puzzle that is due
    (``due_at <= now``) AND incomplete; ``None`` when nothing is
    currently due (every due day solved, or the next day hasn't
    unlocked yet).
    """
    puzzles = sorted(plan.puzzles, key=lambda p: p.day_offset)

    today_puzzle_field: TodayPuzzleResponse | None = None
    for puzzle in puzzles:
        if puzzle.completed_at is None and puzzle.due_at <= now:
            today_puzzle_field = TodayPuzzleResponse(
                day_offset=puzzle.day_offset,
                fen=puzzle.fen,
                expected_move_uci=puzzle.expected_move_uci,
                source_type=puzzle.source_type,
                due_at=puzzle.due_at.isoformat(),
            )
            break

    days = [
        PlanDayResponse(
            day_offset=puzzle.day_offset,
            due_at=puzzle.due_at.isoformat(),
            completed=puzzle.completed_at is not None,
            is_due=puzzle.completed_at is None and puzzle.due_at <= now,
            source_type=puzzle.source_type,
        )
        for puzzle in puzzles
    ]

    return TodayPlanResponse(
        plan_id=plan.id,
        theme=plan.theme,
        verdict=plan.verdict,
        status=plan.status,
        total_days=len(PLAN_DAY_OFFSETS),
        today_puzzle=today_puzzle_field,
        days=days,
    )


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

    return _serialize_plan(plan, datetime.utcnow())


# ---------------------------------------------------------------------------
# POST /coach/plan/puzzle/complete
# ---------------------------------------------------------------------------


class CompletePuzzleRequest(BaseModel):
    """Mark one day's puzzle in a study plan as solved.

    The client calls this AFTER a verified-correct solve
    (``POST /training/verify-replay`` → ``POST /training/solve``), so
    engine truth has already gated the move.  This endpoint records
    plan PROGRESS, not engine truth — same trust posture as
    ``/training/solve`` (the caller asserts the solve; XP and progress
    are personal, idempotent, and carry no cross-user value)."""

    plan_id: str
    day_offset: int


@router.post("/plan/puzzle/complete", response_model=TodayPlanResponse)
@limiter.limit("60/minute")
def complete_puzzle(
    req: CompletePuzzleRequest,
    request: Request,
    player: Player = Depends(get_current_player),
    db: DBSession = Depends(get_db),
) -> TodayPlanResponse:
    """Mark a study-plan puzzle complete and advance the plan.

    Closes the loop the phase-1 scaffold left open: it writes
    ``MistakeStudyPuzzle.completed_at`` so the day stops resurfacing in
    ``GET /coach/plan/today``, and flips the plan to ``STATUS_COMPLETED``
    once every day is solved (so the week ends instead of re-serving
    day 0 forever).

    Idempotent: completing an already-completed puzzle keeps the
    original ``completed_at`` and returns ``200`` with the current
    plan state.  Returns the refreshed plan (including the possibly-new
    ``status == "completed"``) so the client can render the next due
    puzzle — or the week-complete celebration — without a second
    round-trip.

    Errors
    ------
    * ``404`` — no plan with that id owned by the authenticated player
      (ownership is enforced by the ``player_id`` filter, so another
      player's plan is indistinguishable from a missing one), or the
      plan has no puzzle at ``day_offset``.
    """
    plan = (
        db.query(MistakeStudyPlan)
        .filter(
            MistakeStudyPlan.id == req.plan_id,
            MistakeStudyPlan.player_id == player.id,
        )
        .first()
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="study plan not found")

    puzzle = (
        db.query(MistakeStudyPuzzle)
        .filter(
            MistakeStudyPuzzle.plan_id == plan.id,
            MistakeStudyPuzzle.day_offset == req.day_offset,
        )
        .first()
    )
    if puzzle is None:
        raise HTTPException(status_code=404, detail="no puzzle for that day in this plan")

    now = datetime.utcnow()
    if puzzle.completed_at is None:
        puzzle.completed_at = now

    # ``puzzle`` is the same identity-map object as the matching entry
    # in ``plan.puzzles`` (same session, same PK), so this all-done
    # check sees the completed_at we just set.
    all_days_done = all(p.completed_at is not None for p in plan.puzzles)
    if all_days_done and plan.status != STATUS_COMPLETED:
        plan.status = STATUS_COMPLETED
        plan.completed_at = now

    db.commit()
    db.refresh(plan)

    return _serialize_plan(plan, now)
