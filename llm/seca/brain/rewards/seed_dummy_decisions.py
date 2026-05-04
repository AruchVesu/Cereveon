from datetime import datetime, timedelta
import uuid
import random

try:
    from llm.seca.db import SessionLocal
    from llm.seca.models import Player
    from llm.seca.brain.training.models import TrainingDecision
except ModuleNotFoundError:
    from seca.db import SessionLocal
    from seca.models import Player
    from seca.brain.training.models import TrainingDecision


DEFAULT_COUNT = 3
DEFAULT_DAYS_AGO = 21
DEFAULT_STRATEGIES = ["tactics", "calculation", "endgames"]


def _ensure_player(db) -> Player:
    player = db.query(Player).first()
    if player:
        return player

    player = Player(
        email=f"demo_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="demo",
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return player


def seed_decisions(count: int = DEFAULT_COUNT, days_ago: int = DEFAULT_DAYS_AGO):
    db = SessionLocal()
    try:
        player = _ensure_player(db)
        created_at = datetime.utcnow() - timedelta(days=days_ago)

        for _ in range(count):
            decision = TrainingDecision(
                id=str(uuid.uuid4()),
                player_id=player.id,
                created_at=created_at,
                rating_before=float(player.rating),
                confidence_before=float(player.confidence),
                recent_accuracy=0.6,
                weakness_tactics=0.3,
                weakness_time=0.2,
                games_last_week=5,
                strategy=random.choice(DEFAULT_STRATEGIES),
                outcome_ready=0,
            )
            db.add(decision)

        db.commit()
        print(f"Inserted {count} training_decisions for player {player.id}.")

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    seed_decisions()
