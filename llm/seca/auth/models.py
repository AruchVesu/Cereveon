import uuid
from datetime import datetime, timedelta

from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Player(Base):
    __tablename__ = "players"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    rating = Column(Float, default=1200.0)
    confidence = Column(Float, default=0.5)
    skill_vector_json = Column(Text, default="{}")
    player_embedding = Column(Text, default="[]")

    sessions = relationship("Session", back_populates="player")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id = Column(String, ForeignKey("players.id"), index=True)

    token_hash = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow() + timedelta(days=7), index=True)
    device_info = Column(String, default="")

    player = relationship("Player", back_populates="sessions")
