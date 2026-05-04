import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.router import get_db, get_current_player
from llm.seca.events.storage import EventStorage
from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
from llm.seca.analytics.training_recommendations import generate_training_recommendations
from .generator import CurriculumGenerator, _CATEGORY_TO_TOPIC

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/curriculum", tags=["curriculum"])


@router.post("/next")
def next_training(
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    # ---- game-history analysis (deterministic, no RL) ----
    dominant_topic: str | None = None
    recommendations: list[dict] = []
    dominant_category: str | None = None

    recent_games = EventStorage(db).get_recent_games(player_id=str(player.id), limit=20)
    if recent_games:
        try:
            stats = HistoricalAnalysisPipeline(db).run(str(player.id), recent_games)
            dominant_category = stats.dominant_category
            dominant_topic = _CATEGORY_TO_TOPIC.get(dominant_category or "", None)
            recommendations = [
                {"category": r.category, "priority": r.priority, "rationale": r.rationale}
                for r in generate_training_recommendations(stats)
            ]
        except Exception:
            logger.exception("HistoricalAnalysisPipeline failed in curriculum/next; using skill vector")

    plan = CurriculumGenerator(db).generate(str(player.id), dominant_topic=dominant_topic)

    return {
        "topic": plan.topic,
        "difficulty": plan.difficulty,
        "exercise_type": plan.exercise_type,
        "payload": json.loads(plan.payload_json),
        "recommendations": recommendations,
        "dominant_category": dominant_category,
    }
