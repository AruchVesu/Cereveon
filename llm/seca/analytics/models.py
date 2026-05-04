import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship

from ..auth.models import Base


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    player_id = Column(String, ForeignKey("players.id"), nullable=True)

    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)

    player = relationship("Player")
