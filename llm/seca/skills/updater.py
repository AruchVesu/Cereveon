import json
from statistics import mean
from sqlalchemy.orm import Session as DBSession

from llm.seca.learning.player_embedding import (
    PlayerEmbeddingEncoder,
    embedding_from_json,
    embedding_to_json,
)
from llm.seca.brain.bandit.experience_store import ExperienceStore
from llm.seca.brain.bandit.context_builder import build_context_vector

from llm.seca.auth.models import Player
from llm.seca.events.models import GameEvent


class SkillUpdater:
    """
    Updates:
    - rating
    - confidence
    - skill vector
    """

    def __init__(self, db: DBSession):
        self.db = db

    # ---------------------------------
    # Main update after new game
    # ---------------------------------
    def update_from_event(self, player_id: str, event: GameEvent):

        player = self.db.query(Player).filter_by(id=player_id).first()
        if not player:
            return

        rating_before = player.rating
        confidence_before = player.confidence

        # -----------------------------
        # Rating delta (simple Elo-like)
        # -----------------------------
        if event.result == "win":
            delta = 12
        elif event.result == "loss":
            delta = -12
        else:
            delta = 2

        # accuracy influence
        delta += (event.accuracy - 0.5) * 10

        player.rating = max(100.0, player.rating + delta)

        # -----------------------------
        # Confidence update
        # -----------------------------
        confidence_change = (event.accuracy - 0.5) * 0.1
        player.confidence = min(1.0, max(0.0, player.confidence + confidence_change))

        # -----------------------------
        # Skill vector aggregation
        # -----------------------------
        weaknesses = json.loads(event.weaknesses_json)

        current = json.loads(player.skill_vector_json or "{}")

        for k, v in weaknesses.items():
            current[k] = (current.get(k, 0.0) * 0.8) + (v * 0.2)

        player.skill_vector_json = json.dumps(current)

        # -----------------------------
        # Player embedding update
        # -----------------------------
        encoder = PlayerEmbeddingEncoder()
        z_prev = embedding_from_json(player.player_embedding)
        z_new = encoder.encode(
            rating=player.rating,
            confidence=player.confidence,
            accuracy=event.accuracy,
            weaknesses=current,
            z_prev=z_prev,
        )
        player.player_embedding = embedding_to_json(z_new)

        # ---------------------------------------
        # Build context for bandit
        # ---------------------------------------
        context = build_context_vector(
            rating_before=rating_before,
            confidence_before=confidence_before,
            accuracy=event.accuracy,
            weaknesses=weaknesses,
        )

        # ---------------------------------------
        # Define action + reward
        # ---------------------------------------
        # Derive action from the dominant weakness so bandit learns correctly.
        action = max(weaknesses, key=weaknesses.get) if weaknesses else "general"
        reward = player.rating - rating_before

        # ---------------------------------------
        # Log to global brain memory
        # ---------------------------------------
        ExperienceStore(self.db).log(
            player_id=player_id,
            context=context,
            action=action,
            reward=reward,
        )

        self.db.commit()
