"""CoachAgent — the orchestrator for per-mistake study plans.

Phase 1 ships a STUB.  ``generate_plan`` is fully wired but writes:

* ``theme = "generic"`` (phase 2 replaces with an LLM-classified tag)
* ``verdict = ""`` (phase 2 replaces with an LLM-written retrospective)
* All three puzzles point at the mistake FEN+UCI (phase 3 replaces
  day-3 and day-7 with library variants on the same theme)

The wire shape, dedup contract, and scheduling layout are locked here
so phases 2-4 don't have to re-litigate the data model.

Background-task entrypoint
--------------------------
The function is called from ``llm/seca/events/router.py`` via FastAPI's
``BackgroundTasks.add_task`` — runs AFTER the /game/finish response is
sent.  That means:

* Errors are not visible to the user (they only land in logs); the
  function MUST swallow exceptions and log them rather than raise.
* The DB session used by the request handler is already closed.  We
  open a fresh session from ``SessionLocal`` inside the function and
  close it explicitly (no FastAPI ``Depends`` available outside the
  request lifecycle).
* The function is synchronous.  FastAPI's BackgroundTasks runs sync
  callables in the threadpool, which is what we want here — DB writes
  are blocking I/O.

Dedup
-----
A retry of ``/game/finish`` on the same game (e.g. flaky network)
must NOT create a second plan for the same mistake.  Pre-check via the
``UniqueConstraint(player_id, source_event_id)`` index: if a plan
exists, return it idempotently.  If the pre-check races with a
concurrent generator, the unique index promotes the race to an
``IntegrityError`` which we catch + re-fetch (same pattern as
``training/router.py::solve``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from llm.seca.coach.study_plan.models import (
    PLAN_DAY_OFFSETS,
    PUZZLE_SOURCE_ORIGINAL,
    STATUS_ACTIVE,
    MistakeStudyPlan,
    MistakeStudyPuzzle,
)

logger = logging.getLogger(__name__)


def generate_plan(
    *,
    db: DBSession,
    player_id: str,
    source_event_id: str,
    mistake_fen: str,
    played_uci: str,
) -> MistakeStudyPlan | None:
    """Create (or return existing) a 3-puzzle study plan for one mistake.

    Phase 1 stub: all three puzzles share the mistake FEN+UCI,
    ``theme="generic"``, ``verdict=""``.  Status lands at ``active``
    immediately because no LLM call gates the transition yet.

    Parameters
    ----------
    db:
        An open SQLAlchemy session.  The caller owns the lifecycle —
        the background-task entrypoint below opens its own session
        from ``SessionLocal`` and passes it in.
    player_id:
        Authenticated player UUID (string form).  Foreign-key target on
        ``mistake_study_plans.player_id``.
    source_event_id:
        The ``game_events.id`` UUID for the game that produced the
        mistake.  Combined with ``player_id`` for the dedup unique
        constraint.
    mistake_fen:
        The position the player faced before the bad move.  Copied
        verbatim from ``FirstMistake.fen_before``.
    played_uci:
        The move the player actually played in the mistake position
        (UCI).  Copied verbatim from ``FirstMistake.played_uci``.
        Used as the day-0 puzzle's ``expected_move_uci`` — the
        client-side replay sheet uses it to short-circuit a
        re-submission of the same wrong move.

    Returns
    -------
    The created (or existing-duplicate) ``MistakeStudyPlan`` row.
    Returns ``None`` only on an unrecoverable error after logging —
    callers don't need to do anything with a ``None`` because the
    plan is best-effort and the user-facing /game/finish response
    has already been sent.
    """
    # Pre-check: existing plan for the same (player, source_event)?
    # The unique index makes this a cheap point lookup.
    existing = (
        db.query(MistakeStudyPlan)
        .filter(
            MistakeStudyPlan.player_id == player_id,
            MistakeStudyPlan.source_event_id == source_event_id,
        )
        .first()
    )
    if existing is not None:
        return existing

    now = datetime.utcnow()

    plan = MistakeStudyPlan(
        player_id=player_id,
        source_event_id=source_event_id,
        # Phase 1 stub values — phase 2 replaces these via the LLM
        # call before the row is committed.
        theme="generic",
        verdict="",
        status=STATUS_ACTIVE,
        created_at=now,
    )
    db.add(plan)
    # Flush so ``plan.id`` is populated for the puzzle FKs below
    # without committing yet — keeps the plan + 3 puzzles in one txn
    # so a partial failure (puzzle 2 violates a constraint) rolls
    # back the whole thing.
    db.flush()

    for day_offset in PLAN_DAY_OFFSETS:
        puzzle = MistakeStudyPuzzle(
            plan_id=plan.id,
            day_offset=day_offset,
            # Phase 1 stub: every puzzle uses the exact mistake
            # position.  Phase 3 replaces day-3 and day-7 with
            # library variants drawn from a curated YAML corpus.
            fen=mistake_fen,
            expected_move_uci=played_uci,
            source_type=PUZZLE_SOURCE_ORIGINAL,
            due_at=now + timedelta(days=day_offset),
        )
        db.add(puzzle)

    try:
        db.commit()
    except IntegrityError:
        # Race: two concurrent /game/finish retries both passed the
        # pre-check, one committed first, the second hit the unique
        # index.  Roll back, re-fetch, return the winner's row — same
        # observable behaviour as the pre-check dedup path.
        db.rollback()
        existing = (
            db.query(MistakeStudyPlan)
            .filter(
                MistakeStudyPlan.player_id == player_id,
                MistakeStudyPlan.source_event_id == source_event_id,
            )
            .first()
        )
        if existing is None:
            # IntegrityError implies a row exists; not finding one is
            # a real bug.  Loud signal.
            logger.error(
                "study_plan IntegrityError but no row found for player_id=%s source_event_id=%s",
                player_id,
                source_event_id,
            )
            return None
        return existing

    db.refresh(plan)
    return plan


def generate_plan_async(
    *,
    player_id: str,
    source_event_id: str,
    mistake_fen: str,
    played_uci: str,
) -> None:
    """FastAPI ``BackgroundTasks`` entrypoint — runs after /game/finish.

    Opens its own DB session (the request-scoped one is closed by the
    time this fires), invokes ``generate_plan``, and swallows
    exceptions so a generator hiccup never surfaces as a 500 the user
    can't see anyway.

    Imported lazily inside ``llm/seca/events/router.py`` to avoid a
    circular import — this module depends on the SQLAlchemy ``Base``
    through ``llm.seca.auth.models``, and the events router already
    sits at the bottom of the import graph.
    """
    # Lazy import: ``SessionLocal`` lives in auth/router.py which is
    # itself loaded by this module's caller (events/router.py) — a
    # top-level import here would create a cycle.  Inside-function
    # imports of session factories are the project convention for
    # background-task paths.
    from llm.seca.auth.router import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        generate_plan(
            db=db,
            player_id=player_id,
            source_event_id=source_event_id,
            mistake_fen=mistake_fen,
            played_uci=played_uci,
        )
    except Exception:  # noqa: BLE001
        # Plan generation is non-critical: the user-facing
        # /game/finish response has already gone out, and the next
        # /game/finish for the next game will get its own plan.  Log
        # loudly so a regression surfaces in operator logs, but never
        # propagate — BackgroundTasks errors land in the FastAPI
        # logger anyway, and an unswallowed exception here would
        # poison the threadpool worker.
        logger.exception(
            "study_plan generate_plan_async failed for player_id=%s source_event_id=%s",
            player_id,
            source_event_id,
        )
    finally:
        db.close()
