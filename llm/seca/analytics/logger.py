from sqlalchemy.orm import Session
from .models import AnalyticsEvent


class AnalyticsLogger:
    def __init__(self, db: Session):
        self.db = db

    def log(
        self,
        event_type: str,
        payload: dict | None = None,
        player_id: str | None = None,
    ):
        event = AnalyticsEvent(
            event_type=event_type,
            payload=payload or {},
            player_id=player_id,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event
