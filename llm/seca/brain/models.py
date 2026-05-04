import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Integer, Text

from llm.seca.auth.models import Base


class RatingUpdate(Base):
    __tablename__ = "rating_updates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id = Column(String, ForeignKey("game_events.id"), nullable=False)

    rating_before = Column(Float, nullable=False)
    rating_after = Column(Float, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)


class ConfidenceUpdate(Base):
    __tablename__ = "confidence_updates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id = Column(String, ForeignKey("game_events.id"), nullable=False)

    confidence_before = Column(Float, nullable=False)
    confidence_after = Column(Float, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)


class BanditExperience(Base):
    __tablename__ = "bandit_experiences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(String, nullable=True)
    context_json = Column(Text, nullable=False)
    action = Column(String, nullable=False)
    reward = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


__all__ = [
    "RatingUpdate",
    "ConfidenceUpdate",
    "BanditExperience",
]
