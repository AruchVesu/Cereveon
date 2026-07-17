"""SQLAlchemy models for the per-mistake study-plan surface.

Two tables, both on the existing ``Base`` so they participate in the
single ``init_schema`` ``create_all`` pass at lifespan startup:

* ``MistakeStudyPlan`` — one row per (player, source_event) pair.
  Carries the LLM-written verdict + theme tag (phase 2+) and the
  status of the plan as a whole.

* ``MistakeStudyPuzzle`` — three rows per plan, keyed by
  ``day_offset`` ∈ {0, 3, 7}.  Day 0 is the exact mistake position;
  days 3 and 7 are library variants on the same theme (phase 3+).

The ``(player_id, source_event_id)`` UNIQUE constraint on the plan
table is the dedup contract — a retry of ``/game/finish`` on the same
game cannot create a second plan for the same mistake.  The
``(plan_id, day_offset)`` UNIQUE constraint on the puzzle table
guards against a buggy generator double-writing a puzzle slot.

Source-type strings on the puzzle table are aligned with the existing
``llm.seca.training.models.SOURCE_TYPES`` vocabulary so the
``/training/solve`` endpoint can credit XP for solved study-plan
puzzles without a new source-type addition (uses
``source_type="mistake_replay"`` for day 0, mirrors the existing
mistake-replay solve path).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base

if TYPE_CHECKING:
    from llm.seca.auth.models import Player
    from llm.seca.events.models import GameEvent


# Plan status lifecycle.  String column rather than Postgres ENUM for
# the same SQLite/Postgres-parity reason ``TrainingCompletion.source_type``
# is a string (see training/models.py).
STATUS_PENDING_GENERATION: str = "pending_generation"
"""Plan row created but the LLM verdict + theme have not been
populated yet.  Phase 1 ships with the agent still in stub mode, so
plans land directly in ``active`` — this status is reserved for
phase 2 when the background LLM call may be in flight."""

STATUS_ACTIVE: str = "active"
"""Plan is on the schedule; at least one puzzle is incomplete."""

STATUS_COMPLETED: str = "completed"
"""All three puzzles solved.  The plan no longer surfaces on
``GET /coach/plan/today``."""

STATUSES: frozenset[str] = frozenset({STATUS_PENDING_GENERATION, STATUS_ACTIVE, STATUS_COMPLETED})


# Puzzle-source types.  String column for the same parity reason.
PUZZLE_SOURCE_ORIGINAL: str = "original"
"""Puzzle uses the exact FEN of the player's actual mistake — always
day 0 of every plan."""

PUZZLE_SOURCE_LIBRARY: str = "library"
"""Puzzle is a theme-matched variant pulled from the curated puzzle
library (phase 3+).  Phase 1 stub fills days 3 and 7 with
``PUZZLE_SOURCE_ORIGINAL`` rows pointing at the same mistake FEN."""

PUZZLE_SOURCES: frozenset[str] = frozenset({PUZZLE_SOURCE_ORIGINAL, PUZZLE_SOURCE_LIBRARY})


# Day offsets a plan covers.  Constant rather than a config because the
# spaced-repetition shape is part of the product, not a tuning knob —
# and the value flows into the response Pydantic model's docstring +
# the API contract.
PLAN_DAY_OFFSETS: tuple[int, ...] = (0, 3, 7)


class MistakeStudyPlan(Base):
    __tablename__ = "mistake_study_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_event_id: Mapped[str] = mapped_column(
        String, ForeignKey("game_events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Theme tag (e.g. ``"king_safety"``, ``"fork"``, ``"back_rank"``).
    # Phase 1 ships every plan with ``"generic"``; phase 2's LLM call
    # produces a real value from a fixed vocabulary.  This describes the
    # day-0 mistake's motif specifically; the week's overall focus is
    # ``anchor_category`` below.
    theme: Mapped[str] = mapped_column(String, nullable=False, default="generic")

    # The player's aggregate dominant weakness — one of the four
    # ``MistakeCategory`` values (``opening_preparation`` /
    # ``tactical_vision`` / ``positional_play`` / ``endgame_technique``)
    # computed by ``HistoricalAnalysisPipeline`` over recent games at
    # /game/finish.  Surfaced as the week's focus label ("This week:
    # Tactics").  For day-3 / day-7 puzzle SELECTION it is only the
    # BACKFILL pool: the practice puzzles lead with the day-0 mistake's
    # own ``theme`` and fall back to this category's theme set when that
    # theme is too thin (see ``library.pick_two_puzzles_theme_first``).
    # Nullable: legacy plans and plans created when the player has too
    # little history to surface a dominant category (``None``) — the
    # backfill then degrades to the generic bucket.
    anchor_category: Mapped[str | None] = mapped_column(String, nullable=True)

    # LLM-written retrospective verdict explaining the mistake.
    # Phase 1 ships empty (``""``); phase 2's single-shot generation
    # populates it with a <= 100-word, Mode-2-validator-clean string.
    # Stored as ``Text`` not ``String`` because some dialects cap
    # ``VARCHAR`` length and the verdict is open-ended free-form text.
    verdict: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # One of ``STATUSES``.  Validated at the agent / endpoint boundary
    # so the DB column doesn't need a Postgres ENUM migration when
    # phase 2 adds a new status (e.g. ``"failed_generation"``).
    status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_ACTIVE)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships are forward-typed so the Player / GameEvent models
    # don't have to import this module (which would create a circular
    # import — this module imports Base from auth.models).
    player: Mapped["Player"] = relationship("Player")
    source_event: Mapped["GameEvent"] = relationship("GameEvent")
    puzzles: Mapped[list["MistakeStudyPuzzle"]] = relationship(
        "MistakeStudyPuzzle",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="MistakeStudyPuzzle.day_offset",
    )

    __table_args__ = (
        # Dedup: a retry of /game/finish on the same game must not
        # create a second plan for the same mistake.  The unique
        # index also makes the agent's "fetch existing or create new"
        # path a single round-trip on the happy path.
        UniqueConstraint(
            "player_id",
            "source_event_id",
            name="uq_mistake_study_plans_player_source",
        ),
    )


class MistakeStudyPuzzle(Base):
    __tablename__ = "mistake_study_puzzles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(
        String, ForeignKey("mistake_study_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # One of ``PLAN_DAY_OFFSETS``.  The product surface assumes exactly
    # three puzzles per plan; we don't enforce that at the DB level
    # because the agent is the single writer.  The UNIQUE(plan_id,
    # day_offset) constraint at the bottom prevents two rows from
    # landing in the same slot.
    day_offset: Mapped[int] = mapped_column(Integer, nullable=False)

    # The position the puzzle drops the user into.  For day-0 puzzles
    # this is the player's actual mistake FEN; for library variants
    # this is the FEN of the library puzzle.
    fen: Mapped[str] = mapped_column(Text, nullable=False)

    # The engine's preferred move at ``fen``, in UCI.  Used by the
    # client UI to short-circuit re-submission of the same move, and
    # by the (existing) ``/training/verify-replay`` endpoint as the
    # comparison anchor.  For day-0 puzzles this is the player's
    # ORIGINAL bad move (``FirstMistake.played_uci``) — the puzzle
    # asks the user to find a stronger alternative.  For library
    # variants this is the puzzle's expected solution move.
    expected_move_uci: Mapped[str] = mapped_column(String, nullable=False)

    # Full solution walk for library variants, as a space-separated UCI
    # string (solver moves at even indices, opponent replies at odd ones,
    # always ending on a solver move) — e.g. Lichess's complete solution
    # line, so the drill sheet can walk a multi-move puzzle instead of
    # stopping after one move.  Display / walk-through hint only: every
    # solver move the user plays is still judged by the LOCAL engine via
    # ``/training/verify-replay``.  NULL for day-0 originals (their
    # ``expected_move_uci`` is the player's BAD move, not a solution) and
    # for library rows written before this column existed (the router
    # falls back to the single ``expected_move_uci``).
    solution_line_uci: Mapped[str | None] = mapped_column(Text, nullable=True)

    # One of ``PUZZLE_SOURCES``.  Lets the response distinguish
    # "your actual mistake" from "library variant" so the UI can
    # title the puzzle accordingly ("Replay your mistake" vs
    # "Practice: king safety").
    source_type: Mapped[str] = mapped_column(String, nullable=False, default=PUZZLE_SOURCE_ORIGINAL)

    # When this puzzle becomes available.  Computed as
    # ``plan.created_at + timedelta(days=day_offset)`` at plan-creation
    # time so the schedule is stable even if the plan generator is
    # rerun.
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Set when the user solves the puzzle (verified by the existing
    # ``/training/verify-replay`` + ``/training/solve`` path).  Phase 1
    # ships without the wiring that updates this column; phase 4
    # closes the loop.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    plan: Mapped["MistakeStudyPlan"] = relationship("MistakeStudyPlan", back_populates="puzzles")

    __table_args__ = (
        # Each plan has at most one puzzle per day slot.  Guards against
        # a buggy generator double-writing slot 0 (or any slot).
        UniqueConstraint(
            "plan_id",
            "day_offset",
            name="uq_mistake_study_puzzles_plan_day",
        ),
    )
