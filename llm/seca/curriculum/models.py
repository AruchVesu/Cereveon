import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship

from llm.seca.auth.models import Base


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id = Column(String, ForeignKey("players.id"), index=True)

    topic = Column(String, nullable=False)
    difficulty = Column(String, nullable=False)
    exercise_type = Column(String, nullable=False)

    payload_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow)

    player = relationship("Player")
