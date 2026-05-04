from datetime import datetime, timedelta
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import select

try:
    from llm.seca.db import SessionLocal
    from llm.seca.models import Player
    from llm.seca.brain.training.models import TrainingDecision, TrainingOutcome
except ModuleNotFoundError:
    from seca.db import SessionLocal
    from seca.models import Player
    from seca.brain.training.models import TrainingDecision, TrainingOutcome


# how long to wait before measuring reward
REWARD_DELAY_DAYS = 14


def compute_reward(rating_before, rating_after, conf_before, conf_after):
    """
    Minimal stable reward.
    Later we can extend safely.
    """
    rating_delta = rating_after - rating_before
    confidence_delta = conf_after - conf_before

    return rating_delta, confidence_delta


def process_single_decision(db: Session, decision: TrainingDecision):
    """
    Measure outcome for one finished decision.
    """

    player = db.get(Player, decision.player_id)
    if not player:
        return

    rating_delta, confidence_delta = compute_reward(
        decision.rating_before,
        player.rating,
        decision.confidence_before,
        player.confidence,
    )

    outcome = TrainingOutcome(
        id=str(uuid.uuid4()),
        decision_id=decision.id,
        measured_at=datetime.utcnow(),
        rating_after=player.rating,
        confidence_after=player.confidence,
        games_played=0,  # optional future metric
        rating_delta=rating_delta,
        confidence_delta=confidence_delta,
    )

    db.add(outcome)

    # mark decision ready for learning
    decision.outcome_ready = 1


def run_weekly_reward_update():
    """
    Main entrypoint for cron/worker.
    """

    db: Session = SessionLocal()

    try:
        cutoff = datetime.utcnow() - timedelta(days=REWARD_DELAY_DAYS)

        stmt = select(TrainingDecision).where(
            TrainingDecision.outcome_ready == 0,
            TrainingDecision.created_at <= cutoff,
        )

        decisions = db.execute(stmt).scalars().all()

        if not decisions:
            print("No decisions ready for reward measurement.")
            return

        print(f"Processing {len(decisions)} decisions...")

        for decision in decisions:
            process_single_decision(db, decision)

        db.commit()
        print("Weekly reward update complete.")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    run_weekly_reward_update()
