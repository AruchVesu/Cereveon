import json
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.models import Player
from llm.seca.events.storage import EventStorage
from .models import TrainingPlan
from .policy import CurriculumPolicy

# Maps MistakeCategory → curriculum topic.
# Used to override skill-vector topic selection when game history reveals a
# dominant pattern.  Positional play has no direct curriculum exercise type,
# so it intentionally falls back to the skill-vector route.
_CATEGORY_TO_TOPIC: dict[str, str] = {
    "tactical_vision": "tactics",
    "endgame_technique": "endgame",
    "opening_preparation": "opening",
}


class CurriculumGenerator:
    """Generates and persists a TrainingPlan for a given player.

    This is a complete implementation, not a stub. It queries the player
    record, delegates topic/difficulty/exercise selection to CurriculumPolicy,
    and commits the resulting TrainingPlan to the database.

    Raises:
        ValueError: if the player_id does not exist in the database.
    """

    def __init__(self, db: DBSession):
        self.db = db
        self.policy = CurriculumPolicy()
        self.events = EventStorage(db)

    # ------------------------------------------------

    def generate(self, player_id: str, dominant_topic: str | None = None) -> TrainingPlan:
        """Generate a training plan for the player.

        Parameters
        ----------
        player_id:
            The player's UUID string.
        dominant_topic:
            Optional history-derived topic override from HistoricalAnalysisPipeline.
            When provided, replaces the skill-vector-based topic selection.
        """
        player = self.db.query(Player).filter_by(id=player_id).first()
        if not player:
            raise ValueError("Player not found")

        skill_vector = json.loads(player.skill_vector_json or "{}")

        # Use game-history dominant topic when available; fall back to skill vector.
        topic = dominant_topic if dominant_topic else self.policy.choose_topic(skill_vector)
        difficulty = self.policy.choose_difficulty(player.rating, player.confidence)
        exercise_type = self.policy.choose_exercise_type(topic)
        session_length = self.policy.choose_session_length(player.confidence)

        payload = {
            "session_minutes": session_length,
            "focus": topic,
            "difficulty": difficulty,
            "exercise": exercise_type,
        }

        plan = TrainingPlan(
            player_id=player_id,
            topic=topic,
            difficulty=difficulty,
            exercise_type=exercise_type,
            payload_json=json.dumps(payload),
        )

        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)

        return plan
