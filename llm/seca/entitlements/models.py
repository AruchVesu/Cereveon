"""SQLAlchemy models for the entitlements layer (subscription usage metering).

Schema
------
One ``usage_counters`` row per metering scope.  Two row shapes share the
table:

- **Pure counters** (``chat_turn``): one row per
  ``(player, metric, period)`` whose ``count`` increments on use.
  ``subject`` is the empty-string sentinel.
- **Admission markers** (``coached_game`` per game_id,
  ``import_analysis`` per game_event_id — see
  ``service._MARKER_METRICS``): one row per
  ``(player, metric, period, subject)``.  The row's existence — not its
  ``count`` — is the signal: a marker means that unit was admitted to
  the LLM path for the period, so re-asking for the same subject stays
  on the same side of the limit.  (``import_analysis`` was documented
  as a pure counter until PR #390 metered it via ``admit()``; ``check``
  reading the never-written counter row was the "3 reviews left
  forever" bug.)

``period_key`` is a UTC calendar bucket rendered by the (next-subtask)
service layer: ``YYYY-MM-DD`` for daily metrics, ``YYYY-MM`` for
monthly ones.  Rows from expired periods are simply never matched
again; there is no cleanup dependency for correctness.

Why ``subject`` is NOT NULL with an ``""`` sentinel
---------------------------------------------------
The natural shape ("NULL when there is no subject") breaks the
uniqueness guarantee this table exists to provide: unique constraints
treat NULLs as DISTINCT on **both** SQLite and Postgres (pre-15 — and
SQLAlchemy has no portable spelling of Postgres 15's NULLS NOT
DISTINCT), so concurrent writers could stack unlimited duplicate
``(player, metric, period, NULL)`` counter rows.  A NOT NULL ``""``
sentinel makes ``uq_usage_counter_scope`` enforce exactly one counter
row per scope on both dialects, which is what lets the service layer
resolve insert races with a plain ``IntegrityError`` retry.

Foreign keys
------------
``player_id`` references ``players.id`` so deleting the player cascades
the usage rows out with it (same GDPR-scope reasoning as
``chat_turns``).  ``subject`` is intentionally NOT a foreign key to
``games`` — metering must survive a game row being pruned, mirroring
``chat_turns.game_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from llm.seca.auth.models import Base


class UsageCounter(Base):
    """One metering row — a pure counter or a per-game admission marker."""

    __tablename__ = "usage_counters"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # Closed vocabulary owned by the entitlements service:
    # "coached_game" / "chat_turn" / "import_analysis".  String rather
    # than Enum so adding a metric is a service-layer change, not a
    # schema migration (same reasoning as ``chat_turns.mode``).
    metric: Mapped[str] = mapped_column(String(32), nullable=False)

    # UTC calendar bucket: "YYYY-MM-DD" (daily) or "YYYY-MM" (monthly).
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)

    # Admission-marker discriminator (the game_id for "coached_game");
    # "" for pure counters — see the module docstring for why the
    # sentinel is NOT NULL.
    subject: Mapped[str] = mapped_column(String, default="", nullable=False)

    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "player_id", "metric", "period_key", "subject", name="uq_usage_counter_scope"
        ),
    )
