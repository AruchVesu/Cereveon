"""SQLAlchemy model for in-app notifications.

One ``Notification`` row per feed entry.  The feed is the app's primary
communication channel (communication & access spec §5): the Android
client renders the rows in a bottom sheet behind the Home-screen bell
and deep-links via the ``action`` key — there is no push transport and
no email transport behind this table in v1.

Design notes
------------
* ``action`` is an app-internal deep-link KEY (``"open_history"``,
  ``"lichess_reconnect"``), not a URL — the client has no URL router,
  it maps keys to sheets.  Kept as a plain column (not inside the JSON
  payload) because the service layer filters on it: system-alert
  dedup and resolution identify the Lichess-disconnected alert by
  ``(type, action)`` without parsing JSON.
* ``metadata_json`` is JSON-in-TEXT (not Postgres JSONB) for SQLite/
  Postgres parity, matching ``GameReview``'s payload columns.
* ``read_at`` / ``dismissed_at`` / ``expires_at`` are the three
  lifecycle timestamps from the spec: read rows stay in the feed,
  dismissed rows leave the feed (soft delete), expired rows leave the
  feed automatically.  ``expires_at IS NULL`` means "until resolved" —
  used by the Lichess-disconnected alert, which the import service
  resolves (dismisses) when connectivity is proven again.
* String columns (not Enum) for ``type`` / ``priority`` — same
  dialect-parity rationale as ``LichessImportJob.status``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player

#: Notification ``type`` vocabulary (closed; owned by this module).
#: v1 ships the two types whose triggers exist in this codebase —
#: the spec's remaining types (welcome, coach_insight, milestone,
#: offer, credit_reset, saved_review_ready) have no producing feature
#: yet and must not be minted ad hoc.
TYPE_GAME_ANALYZED = "game_analyzed"
TYPE_SYSTEM_ALERT = "system_alert"

#: ``priority`` vocabulary (spec §5.1).  The badge counts medium and
#: above; low exists for future types (e.g. offers) so the wire shape
#: doesn't change when they arrive.
PRIORITY_LOW = "low"
PRIORITY_MEDIUM = "medium"
PRIORITY_HIGH = "high"
PRIORITY_CRITICAL = "critical"

#: Priorities that count toward the bell badge (spec §5.6: "Badge shows
#: count of medium+ unread notifications").
BADGE_PRIORITIES = (PRIORITY_MEDIUM, PRIORITY_HIGH, PRIORITY_CRITICAL)

#: Deep-link action keys the Android client understands.
ACTION_OPEN_HISTORY = "open_history"
ACTION_LICHESS_RECONNECT = "lichess_reconnect"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id"), nullable=False, index=True
    )

    type: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False, default=PRIORITY_MEDIUM)

    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: App-internal deep-link key + CTA label; both optional (an
    #: informational row renders without a button).
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    action_label: Mapped[str | None] = mapped_column(String, nullable=True)

    #: JSON-in-TEXT context payload (e.g. ``{"games_analyzed": 3}``).
    #: Named ``metadata_json`` because ``metadata`` is reserved on
    #: SQLAlchemy declarative classes.
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    player: Mapped["Player"] = relationship("Player")

    __table_args__ = (
        # Feed query: WHERE player_id = ? AND dismissed_at IS NULL
        # ORDER BY created_at DESC — the spec's suggested composite.
        Index("ix_notifications_feed", "player_id", "dismissed_at", "created_at"),
    )
