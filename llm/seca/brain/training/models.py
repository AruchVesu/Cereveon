from sqlalchemy import Column, String, DateTime, Float, Integer, ForeignKey, Index

from llm.seca.auth.models import Base


class TrainingDecision(Base):
    __tablename__ = "training_decisions"
    __table_args__ = (
        Index("idx_training_decisions_player", "player_id"),
        Index("idx_training_decisions_ready", "outcome_ready"),
    )

    id = Column(String, primary_key=True)
    player_id = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)

    rating_before = Column(Float, nullable=False)
    confidence_before = Column(Float, nullable=False)
    recent_accuracy = Column(Float)
    weakness_tactics = Column(Float)
    weakness_time = Column(Float)
    games_last_week = Column(Integer)

    strategy = Column(String, nullable=False)
    outcome_ready = Column(Integer, default=0)


class TrainingOutcome(Base):
    __tablename__ = "training_outcomes"
    __table_args__ = (Index("idx_training_outcomes_decision", "decision_id"),)

    id = Column(String, primary_key=True)
    decision_id = Column(String, ForeignKey("training_decisions.id"), nullable=False)
    measured_at = Column(DateTime, nullable=False)

    rating_after = Column(Float, nullable=False)
    confidence_after = Column(Float, nullable=False)
    games_played = Column(Integer)

    rating_delta = Column(Float, nullable=False)
    confidence_delta = Column(Float, nullable=False)
