import json
import logging

from sqlalchemy.orm import Session as DBSession

from llm.seca.adaptation.coupling import compute_adaptation
from llm.seca.learning.player_embedding import (
    PlayerEmbeddingEncoder,
    embedding_from_json,
    embedding_to_json,
)
from llm.seca.brain.bandit.experience_store import ExperienceStore
from llm.seca.brain.bandit.context_builder import build_context_vector
from llm.seca.skills.elo import (
    actual_score_from_result,
    apply_rating_delta,
    compute_rating_delta,
)

from llm.seca.auth.models import Player
from llm.seca.events.models import GameEvent

logger = logging.getLogger(__name__)


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

        # ``with_for_update()`` serialises concurrent rating writes for the
        # same player (audit 2026-07-14, P2 #7): the Elo delta depends on
        # ``rating_before`` (expected-score curve), so two /game/finish
        # requests interleaving read-compute-write from the same base would
        # silently drop one delta.  The row lock is held until the caller's
        # commit — a few ms of handler work.  Postgres honours FOR UPDATE;
        # SQLite ignores it (single-writer database, so the race cannot
        # occur there anyway).
        player = self.db.query(Player).filter_by(id=player_id).with_for_update().first()
        if not player:
            return

        rating_before = player.rating
        confidence_before = player.confidence

        # ``event.accuracy`` and ``event.weaknesses_json`` are typed as
        # ``Optional`` on the GameEvent model (column ``nullable`` is
        # the default ``True``) but in practice are always populated by
        # EventStorage.store_game with defaults of ``0.0`` and ``"{}"``.
        # Coerce to the concrete types here so the downstream arithmetic
        # / json.loads calls have float / str inputs as their signatures
        # require.  Sprint 6.A follow-up: pinning Optional handling so
        # events/router.py (which calls this method) can clear mypy.
        accuracy: float = float(event.accuracy) if event.accuracy is not None else 0.0
        weaknesses_json: str = event.weaknesses_json or "{}"

        # -----------------------------
        # Rating delta — standard Elo
        # -----------------------------
        # PR #174 (2026-05-16) replaced the homebrew
        # ``±12 + (accuracy − 0.5) * 10`` formula with FIDE-style
        # classic Elo so the rating in this app tracks the same
        # scale players see on chess.com / lichess.  The new math
        # uses opponent rating (from the SECA adaptive engine) and
        # banded K-factor (40 for new players, 20 for established,
        # 10 for masters) — see ``llm.seca.skills.elo`` for the full
        # rationale and the pinned tests.
        #
        # Accuracy intentionally NO LONGER influences the rating
        # delta directly — neither chess.com nor lichess use a
        # per-move accuracy signal in their Elo math.  Engine
        # accuracy still flows into ``player.confidence`` below and
        # into the bandit context vector (build_context_vector),
        # where it can shape coaching without distorting the
        # external-scale rating.
        adaptation = compute_adaptation(rating_before, confidence_before)
        opponent_rating = float(adaptation["opponent"]["target_elo"])

        # Total stored games INCLUDES the just-finished one because
        # ``EventStorage.store_game`` commits before SkillUpdater
        # runs (see ``llm/seca/events/router.py:finish_game``).
        # Subtract 1 so the K-factor reflects prior games — at the
        # very first finished game the player has 0 prior games and
        # K=40 (new).  Pinned by
        # ``test_skill_updater_new_player_uses_k40`` in
        # test_elo_integration.
        total_games = (
            self.db.query(GameEvent)
            .filter(GameEvent.player_id == player_id)
            .count()
        )
        prior_games = max(0, total_games - 1)

        if event.result not in ("win", "loss", "draw"):
            # Unknown result — log so operators can investigate the
            # upstream validation gap.  ``actual_score_from_result``
            # collapses unknowns to 0.5 (draw) so the rating delta
            # is unbiased rather than fabricating a win/loss.
            logger.warning(
                "SkillUpdater received unrecognised result %r; treating as draw",
                event.result,
            )
        actual = actual_score_from_result(event.result)

        delta = compute_rating_delta(
            player_rating=rating_before,
            opponent_rating=opponent_rating,
            actual_score=actual,
            games_played=prior_games,
        )

        player.rating = apply_rating_delta(rating_before, delta)

        # -----------------------------
        # Confidence update
        # -----------------------------
        confidence_change = (accuracy - 0.5) * 0.1
        player.confidence = min(1.0, max(0.0, player.confidence + confidence_change))

        # -----------------------------
        # Skill vector aggregation
        # -----------------------------
        weaknesses = json.loads(weaknesses_json)

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
            accuracy=accuracy,
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
            accuracy=accuracy,
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
