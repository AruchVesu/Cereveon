import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..auth.models import Base, Player


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("players.id", ondelete="CASCADE"), nullable=True
    )

    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")


class WeeklyDigest(Base):
    """Materialised weekly coaching digest for a single player.

    A row captures the deterministic vulnerability profile (top categories
    by score) plus the targeted microtasks emitted for the trailing
    ``window_days`` of play. Engine truth comes from the per-game
    ``GameEvent.weaknesses_json`` already produced at finish-time — the
    digest is pure aggregation + selection + template lookup, no LLM.
    """

    __tablename__ = "weekly_digests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )

    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    games_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    holes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")
