"""SQLAlchemy models for the Lichess integration.

* ``LinkedAccount`` — player ↔ external chess-platform link.
* ``LichessImportJob`` — async-import job row (v2 endpoint), one row per
  ``POST /lichess/import`` call; advanced by a thread-pool worker so the
  HTTP request can return 202 immediately.

LinkedAccount constraints
-------------------------
``(player_id, platform)`` is unique: one Lichess link per player.  A
second link request with a different username overwrites — handled at
the service layer by deleting + re-inserting so calibration re-fires.

``(platform, external_username)`` is unique: one Lichess account can be
attached to at most one ChessCoach player at a time.  If user B tries to
link a handle user A already owns, the service layer returns 409 instead
of a 500 from the constraint violation.

LichessImportJob constraints
----------------------------
Exactly one non-terminal (``queued`` / ``running``) job per player at a
time.  Enforced on Postgres via a partial unique index created in
``init_schema`` (SQLite cannot express the predicate portably so dev
relies on the in-process per-player lock in
``llm.seca.lichess.get_player_import_lock``).  The lock is the primary
guard on both dialects; the index is defense-in-depth on prod against
a future caller that bypasses the service entry point.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player


class LinkedAccount(Base):
    __tablename__ = "linked_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), nullable=False, index=True
    )
    # 'lichess' for MVP; future: 'chesscom', etc.
    platform: Mapped[str] = mapped_column(String, nullable=False)
    external_username: Mapped[str] = mapped_column(String, nullable=False)

    # Watermark used as ``since=`` on the next incremental fetch.  None
    # until the first successful import completes.  We persist the
    # timestamp of the newest game seen rather than ``now()`` so a
    # paused import doesn't silently skip games created during the
    # outage window.
    last_imported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")

    __table_args__ = (
        UniqueConstraint("player_id", "platform", name="uq_linked_accounts_player_platform"),
        UniqueConstraint(
            "platform", "external_username", name="uq_linked_accounts_platform_username"
        ),
    )


# Job status string constants.  String column (not Enum) for SQLite/
# Postgres parity — Postgres ENUM types require an explicit migration to
# add a new value, and we expect to add ``cancelled`` later.
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"

JOB_STATUS_TERMINAL = frozenset({JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED})
JOB_STATUS_ACTIVE = frozenset({JOB_STATUS_QUEUED, JOB_STATUS_RUNNING})


class LichessImportJob(Base):
    __tablename__ = "lichess_import_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), nullable=False, index=True
    )

    # One of JOB_STATUS_*.  No CHECK constraint — the value space is
    # small and validated at the service layer; adding a portable
    # CHECK across SQLite + Postgres adds friction without value.
    status: Mapped[str] = mapped_column(String, nullable=False, default=JOB_STATUS_QUEUED)

    # Running counters updated by the worker after each game.  Default
    # 0 (not NULL) so the response shape is well-defined the moment a
    # row exists.
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Games engine-analysed by the post-stream pass (accuracy + weakness
    # vector written back onto the GameEvent rows so the historical
    # analysis pipeline can consume imported games).  Counts THIS job's
    # analysis work, which may include backlog rows from earlier imports
    # that predate the analysis feature.  Nullable=False via default so
    # serialize_job never emits null; legacy rows get the column via the
    # init_schema ADD COLUMN migration with DEFAULT 0.
    analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The cap requested by the client.  Pinned at row creation so the
    # progress-bar denominator is stable across the job's lifetime even
    # if a future feature accepts a re-cap mid-run.
    target_max_games: Mapped[int] = mapped_column(Integer, nullable=False)

    # Newest game's ``createdAt`` (Unix ms) captured during this run,
    # mirrored from the legacy in-loop variable in ``import_user_games``.
    # Promoted to the LinkedAccount watermark on clean success only.
    # BigInteger because Lichess timestamps are 13-digit ms (already past
    # 32-bit signed in normal years).
    last_imported_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # First 500 chars of the exception that aborted the run, or
    # ``"link removed during import"`` when ``unlink_account`` cancels.
    # Truncated to bound a hostile / pathological message.
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    player: Mapped["Player"] = relationship("Player")
