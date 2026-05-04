import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Float, Text, ForeignKey
from sqlalchemy.orm import relationship

from llm.seca.auth.models import Base


class GameEvent(Base):
    __tablename__ = "game_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    player_id = Column(String, ForeignKey("players.id"), index=True)

    # raw PGN or compact move list
    pgn = Column(Text, nullable=False)

    # result: win / loss / draw
    result = Column(String, nullable=False)

    # engine accuracy / centipawn loss etc.
    accuracy = Column(Float, default=0.0)

    # detected weaknesses JSON
    weaknesses_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow)

    player = relationship("Player")
