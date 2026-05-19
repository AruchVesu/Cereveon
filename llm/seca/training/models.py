"""SQLAlchemy models + constants for the training-completion surface.

A ``TrainingCompletion`` row is one verified-solve event credited to a
player.  The running total of ``xp_awarded`` across rows must equal the
``Player.training_xp`` counter — the counter is the cheap read for the
Home Level/XP kicker, the row history is the audit trail.

Source types
------------
``source_type`` is a string column (not a Postgres ENUM) for SQLite/
Postgres parity — adding a future type (e.g. ``daily_streak``) becomes a
constants update rather than a DB migration.  The allowed set lives in
``SOURCE_TYPES`` and is validated at the HTTP boundary so we never
write a value the router didn't accept.

Idempotency
-----------
``(player_id, source_type, source_ref)`` is unique when ``source_ref``
is non-null: the same puzzle / mistake-replay cannot be credited twice
even if the Android client retries on a flaky connection.  Rows with
``source_ref=NULL`` are not deduped — that variant is reserved for free-
form completions where the caller doesn't yet have a stable identifier,
and we accept the tradeoff that duplicates can land for those.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player

# Fixed XP per verified solve.  Single source of truth so test fixtures
# don't bake the literal into every assertion (a future tuning pass
# should change this constant and one or two boundary tests, not 30
# scattered ``assert xp == 10`` checks).
XP_PER_SOLVE: int = 10


# Allowed values for ``TrainingCompletion.source_type``.  Phase 2 ships
# all three names so Phase 3+ callers don't have to migrate the column
# enum when they land — they just start writing rows with the matching
# string.
SOURCE_TYPE_MISTAKE_REPLAY: str = "mistake_replay"
SOURCE_TYPE_WEEKLY_MICROTASK: str = "weekly_microtask"
SOURCE_TYPE_STANDARD_PUZZLE: str = "standard_puzzle"

SOURCE_TYPES: frozenset[str] = frozenset(
    {
        SOURCE_TYPE_MISTAKE_REPLAY,
        SOURCE_TYPE_WEEKLY_MICROTASK,
        SOURCE_TYPE_STANDARD_PUZZLE,
    }
)


class TrainingCompletion(Base):
    __tablename__ = "training_completions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), nullable=False, index=True
    )

    # One of ``SOURCE_TYPES``.  Validated at the HTTP boundary; the
    # database column is plain ``String`` so a future addition (e.g.
    # ``daily_streak``) doesn't need a Postgres ENUM migration.
    source_type: Mapped[str] = mapped_column(String, nullable=False)

    # Stable identifier for the solved item — e.g. ``"game_<id>:move_<n>"``
    # for a mistake replay, the puzzle's external id for a standard
    # puzzle, the digest row's id for a weekly micro-task.  Nullable
    # because some future source types (catch-all freeform) may not
    # have a meaningful ref; rows with NULL ref are NOT deduped.
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    # XP awarded for this single completion.  Denormalised so an XP-
    # curve change in the future doesn't rewrite history — historic rows
    # keep the value they were credited with at the time of the solve.
    xp_awarded: Mapped[int] = mapped_column(Integer, nullable=False)

    completed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")

    __table_args__ = (
        # Idempotency guard: same (player, source_type, source_ref)
        # triple can only be credited once.  Postgres treats NULL as
        # distinct from NULL in unique indexes, so source_ref=NULL rows
        # are allowed to duplicate — that matches the intent above.
        UniqueConstraint(
            "player_id",
            "source_type",
            "source_ref",
            name="uq_training_completions_player_source",
        ),
    )
