"""CoachAgent — the orchestrator for per-mistake study plans.

Phase 1 shipped the data model + dedup contract + scheduling.  Phase 2
(this commit) adds the LLM-generated coach verdict + theme tag:

* ``theme`` — populated by a single-shot ``llm.generate`` call,
  collapsed to ``"generic"`` if the LLM picks a tag outside
  ``study_plan.verdict.THEME_VOCABULARY``.
* ``verdict`` — ≤ 60-word retrospective, gated by Mode-2 negative +
  output firewall.  Empty string when the LLM was unreachable or
  failed validators on both attempts; the Home-screen card simply
  hides the coach-note line in that case.

Phase 3 will replace the day-3 / day-7 puzzles (currently stub copies
of the mistake position) with theme-matched library variants.

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
from typing import TYPE_CHECKING

import chess
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.coach.study_plan.library import (
    LibraryPuzzle,
    load_library,
    pick_two_puzzles_theme_first,
)
from llm.seca.coach.study_plan.models import (
    PLAN_DAY_OFFSETS,
    PUZZLE_SOURCE_LIBRARY,
    PUZZLE_SOURCE_ORIGINAL,
    STATUS_ACTIVE,
    MistakeStudyPlan,
    MistakeStudyPuzzle,
)
from llm.seca.coach.study_plan.verdict import (
    generate_verdict,
    skill_hint_for_rating,
)

if TYPE_CHECKING:
    from llm.rag.llm.base import BaseLLM


# Phase 3: module-level singleton.  Load the curated YAML corpus once
# at import time so per-plan path is a cheap dict lookup.  A malformed
# YAML crashes the import (loud failure at server boot, not a silent
# skip), which surfaces corpus drift in CI rather than in prod logs.
_LIBRARY: dict[str, list[LibraryPuzzle]] = load_library()

logger = logging.getLogger(__name__)


def generate_plan(
    *,
    db: DBSession,
    player_id: str,
    source_event_id: str,
    mistake_fen: str,
    played_uci: str,
    dominant_category: str | None = None,
    llm: "BaseLLM | None" = None,
    library: dict[str, list[LibraryPuzzle]] | None = None,
) -> MistakeStudyPlan | None:
    """Create (or return existing) a 3-puzzle study plan for one mistake.

    Phase 2 wires the LLM verdict path: after the plan + 3 puzzle
    rows commit, the agent calls ``generate_verdict`` with the
    player's rating-derived skill hint and updates ``plan.theme`` +
    ``plan.verdict`` in a second commit.  If the LLM path fails for
    any reason, the plan stays at the phase-1 stub values
    (``theme="generic"``, ``verdict=""``) — the plan is still usable;
    the Home card just hides the coach-note line.

    The day-3 / day-7 puzzles still mirror the mistake FEN (phase 3
    will replace them with theme-matched library variants).

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
    dominant_category:
        The player's aggregate biggest weakness across recent games —
        one of the four ``MistakeCategory`` values, computed by
        ``HistoricalAnalysisPipeline`` at /game/finish.  Stored in
        ``plan.anchor_category`` (surfaced as the week's focus label) and
        used as the BACKFILL pool for day-3 / day-7 selection: the
        practice puzzles lead with the day-0 mistake's OWN LLM-classified
        theme (``king_safety`` etc.), and fall back to this category's
        theme set only when that theme is too thin to fill both days
        (``pick_two_puzzles_theme_first``).  Day 0 always stays the
        player's real mistake position.  ``None`` (new player / too
        little history) just means the backfill degrades to the generic
        bucket.
    llm:
        Optional ``BaseLLM`` instance.  ``None`` (the default) skips
        the verdict generation step and leaves the plan at the
        phase-1 stub values — useful for unit tests that only care
        about the plan/puzzle plumbing.  Production callers
        (``generate_plan_async``) construct a ``DeepseekLLM`` and
        inject it here.  Tests that need verdict-path coverage
        inject a ``FakeLLM`` with rigged output.

    Returns
    -------
    The created (or existing-duplicate) ``MistakeStudyPlan`` row.
    Returns ``None`` only on an unrecoverable error after logging —
    callers don't need to do anything with a ``None`` because the
    plan is best-effort and the user-facing /game/finish response
    has already been sent.
    """
    # One active plan per player.  While a week is in progress, don't
    # mint a new plan — let the player finish it.  The next /game/finish
    # AFTER the active plan completes starts a fresh week anchored on the
    # THEN-current dominant weakness.  (Best-effort: two games finishing
    # near-simultaneously could both pass this check before either plan
    # commits and create two active plans; /coach/plan/today shows the
    # most recent, same benign posture as before.)
    active = (
        db.query(MistakeStudyPlan)
        .filter(
            MistakeStudyPlan.player_id == player_id,
            MistakeStudyPlan.status == STATUS_ACTIVE,
        )
        .order_by(MistakeStudyPlan.created_at.desc())
        .first()
    )
    if active is not None:
        logger.info(
            "study_plan skipped for player_id=%s: active plan %s (created %s) still in progress",
            player_id,
            active.id,
            active.created_at,
        )
        return active

    # Idempotency for a double-fired background task on the SAME game
    # (the unique (player, source_event) index also enforces this at the
    # DB level).  Catches the case where this game's plan already
    # completed and the task re-fires: return the existing plan rather
    # than minting a duplicate for the same source event.
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
        # The week's focus (aggregate dominant weakness).  ``None`` when
        # the player has too little history — the library selector then
        # falls back to the day-0 mistake's own theme.
        anchor_category=dominant_category,
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
            # Spaced-repetition schedule: day 0 unlocks at creation;
            # days 3 / 7 unlock 3 / 7 calendar days later.  The router
            # ALSO enforces sequential order (day 0 before day 3 before
            # day 7), so ``due_at`` is the earliest moment a day can
            # open, not a promise that it is open.  (Pacing was briefly
            # all-at-creation / solve-to-unlock — PR #322 — which let a
            # whole week be cleared in one sitting; the calendar gate is
            # deliberately back.)
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

    # Phase 2: populate ``theme`` + ``verdict`` via a single-shot LLM
    # call.  Best-effort — any failure (LLM unreachable, validator
    # rejected twice, JSON parse failed) leaves the row at the phase-1
    # stub values.  The verdict module returns the fallback tuple
    # ``("generic", "")`` on its own error paths, so we can blindly
    # write whatever it returns without re-checking.
    if llm is not None:
        _populate_verdict(
            db=db,
            plan=plan,
            player_id=player_id,
            mistake_fen=mistake_fen,
            played_uci=played_uci,
            llm=llm,
        )

        # Phase 3: replace day-3 / day-7 puzzles with theme- AND
        # side-matched variants.  Prefers ready-made puzzles fetched live
        # from Lichess (matched to the day-0 mistake's side); falls back to
        # the local curated corpus.  Runs AFTER the verdict commit so the
        # selector sees the LLM-classified theme.  Defensive: any fetch /
        # selector / commit failure leaves day-3 / day-7 at the phase-1 stub
        # (original mistake position).
        active_library = library if library is not None else _LIBRARY
        _populate_library_variants(
            db=db,
            plan=plan,
            player_id=player_id,
            library=active_library,
        )

    return plan


def _populate_verdict(
    *,
    db: DBSession,
    plan: MistakeStudyPlan,
    player_id: str,
    mistake_fen: str,
    played_uci: str,
    llm: "BaseLLM",
) -> None:
    """Second-pass write that fills in the LLM-generated theme + verdict.

    Split out from ``generate_plan`` so the LLM call doesn't sit
    inside the plan/puzzle transaction — if the verdict commit raises,
    the plan + puzzles are already durable and the user still gets a
    usable (degraded) plan.  All exceptions are swallowed: this path
    is non-critical and the caller (background task) can't surface
    a failure to the user anyway.
    """
    try:
        player = db.query(Player).filter(Player.id == player_id).first()
        rating = float(player.rating) if player is not None else 1500.0
        skill_hint = skill_hint_for_rating(rating)

        theme, verdict = generate_verdict(
            mistake_fen=mistake_fen,
            played_uci=played_uci,
            player_skill_hint=skill_hint,
            llm=llm,
        )

        plan.theme = theme
        plan.verdict = verdict
        db.commit()
        db.refresh(plan)
    except Exception:  # noqa: BLE001
        # LLM ran but DB write failed, or any other unexpected hiccup.
        # Roll back so the failed write doesn't poison the txn for
        # whatever the caller does next, but DON'T propagate — the
        # plan + puzzles are already committed and the user-facing
        # /game/finish response has shipped.
        logger.exception(
            "study_plan _populate_verdict failed for plan_id=%s player_id=%s",
            plan.id,
            player_id,
        )
        db.rollback()


def _side_to_move(fen: str) -> chess.Color | None:
    """Side to move in ``fen`` (the day-0 mistake position = the player's
    side), or ``None`` if the FEN doesn't parse.  The detector already
    validated the FEN at /game/finish, so ``None`` is only a defensive
    guard — it just skips the side-matched live path and uses the corpus."""
    try:
        return chess.Board(fen).turn
    except ValueError:
        return None


def _populate_library_variants(
    *,
    db: DBSession,
    plan: MistakeStudyPlan,
    player_id: str,
    library: dict[str, list[LibraryPuzzle]],
) -> None:
    """Phase 3: replace day-3 / day-7 stub puzzles with theme- AND
    side-matched practice variants.  Day 0 (the player's actual mistake) is
    never touched.

    Two sources, tried in order:

    1. **Live Lichess** (``lichess_puzzles.fetch_side_matched_variants``) —
       ready-made puzzles matched to BOTH the day-0 mistake's theme and its
       side-to-move ("the weakest player's side").  Best-effort: used only
       when it yields a full pair, so day 3 / day 7 land on one consistent
       side.
    2. **Local curated corpus** (``library.pick_two_puzzles_theme_first``) —
       the deterministic fallback when Lichess is disabled, unreachable, or
       can't supply two side-matched puzzles.  Leads with the day-0 theme and
       backfills from ``plan.anchor_category`` then ``"generic"``.

    The whole method runs at most once per plan (a re-fired BackgroundTask
    short-circuits on the existing-plan guard in ``generate_plan`` before
    reaching here), so the live source's non-determinism can't reshuffle a
    live user's schedule.

    When both sources come up empty, no update happens — the day-3 / day-7
    rows stay at the phase-1 stub (original mistake position).
    """
    try:
        player = db.query(Player).filter(Player.id == player_id).first()
        rating = float(player.rating) if player is not None else 1500.0
        skill_hint = skill_hint_for_rating(rating)

        db.refresh(plan)
        by_offset = {p.day_offset: p for p in plan.puzzles}

        # The player's side is the side to move in the day-0 mistake position.
        day0 = by_offset.get(0)
        side_to_move = _side_to_move(day0.fen) if day0 is not None else None

        puzzle_day_3: LibraryPuzzle | None = None
        puzzle_day_7: LibraryPuzzle | None = None

        # Source 1: live Lichess, theme- + side-matched.  Only taken when it
        # returns a full pair (see fetch_side_matched_variants); anything less
        # falls through to the corpus rather than mixing sides.
        if side_to_move is not None:
            from llm.seca.coach.study_plan.lichess_puzzles import (  # noqa: PLC0415
                fetch_side_matched_variants,
            )

            variants = fetch_side_matched_variants(
                theme=plan.theme,
                side_to_move=side_to_move,
                skill_hint=skill_hint,
            )
            if len(variants) >= 2:
                puzzle_day_3, puzzle_day_7 = variants[0], variants[1]
                logger.info(
                    "study_plan days 3/7 sourced from Lichess (theme=%s side=%s)",
                    plan.theme,
                    "white" if side_to_move == chess.WHITE else "black",
                )

        # Source 2: local corpus fallback (theme-first, category + generic
        # backfill).  Deterministic per plan_id.
        if puzzle_day_3 is None or puzzle_day_7 is None:
            puzzle_day_3, puzzle_day_7 = pick_two_puzzles_theme_first(
                library=library,
                theme=plan.theme,
                fallback_category=plan.anchor_category,
                skill_hint=skill_hint,
                plan_id=plan.id,
            )

        if puzzle_day_3 is None or puzzle_day_7 is None:
            logger.info(
                "study_plan no library puzzles for theme=%s; days 3/7 stay at mistake position",
                plan.theme,
            )
            return

        for offset, picked in ((3, puzzle_day_3), (7, puzzle_day_7)):
            row = by_offset.get(offset)
            if row is None:
                continue
            row.fen = picked.fen
            row.expected_move_uci = picked.expected_move_uci
            # Full solution walk (Lichess line, or a curated YAML line).  A
            # single-decision puzzle stores just its expected move so the
            # wire shape is uniform for every library row; day-0 keeps NULL
            # (its expected move is the player's BAD move, not a solution).
            row.solution_line_uci = " ".join(
                picked.solution_line_uci or (picked.expected_move_uci,)
            )
            row.source_type = PUZZLE_SOURCE_LIBRARY
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "study_plan _populate_library_variants failed for plan_id=%s player_id=%s",
            plan.id,
            player_id,
        )
        db.rollback()


def generate_plan_async(
    *,
    player_id: str,
    source_event_id: str,
    mistake_fen: str,
    played_uci: str,
    dominant_category: str | None = None,
    llm: "BaseLLM | None" = None,
) -> None:
    """FastAPI ``BackgroundTasks`` entrypoint — runs after /game/finish.

    Opens its own DB session (the request-scoped one is closed by the
    time this fires), constructs a default ``DeepseekLLM`` if the
    caller didn't inject one, invokes ``generate_plan``, and swallows
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

    # Default LLM construction also lazy so unit tests of
    # ``generate_plan`` itself (which inject a FakeLLM) don't pay the
    # cost of importing the DeepSeek adapter.
    if llm is None:
        from llm.rag.llm.deepseek import DeepseekLLM  # noqa: PLC0415

        llm = DeepseekLLM()

    db = SessionLocal()
    try:
        generate_plan(
            db=db,
            player_id=player_id,
            source_event_id=source_event_id,
            mistake_fen=mistake_fen,
            played_uci=played_uci,
            dominant_category=dominant_category,
            llm=llm,
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
