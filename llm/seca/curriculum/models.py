import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm.seca.auth.models import Base, Player


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("players.id", ondelete="CASCADE"), index=True
    )

    topic: Mapped[str] = mapped_column(String, nullable=False)
    difficulty: Mapped[str] = mapped_column(String, nullable=False)
    exercise_type: Mapped[str] = mapped_column(String, nullable=False)

    payload_json: Mapped[str | None] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship("Player")
