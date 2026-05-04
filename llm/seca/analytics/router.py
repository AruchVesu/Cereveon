import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.router import get_db, get_current_player
from llm.seca.events.storage import EventStorage
from llm.seca.brain.models import RatingUpdate, ConfidenceUpdate
from llm.seca.events.models import GameEvent
from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
from llm.seca.analytics.training_recommendations import generate_training_recommendations
from llm.seca.adaptation.coupling import compute_adaptation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/player", tags=["player"])

_TIER = {"simple": "beginner", "intermediate": "intermediate", "advanced": "advanced"}


@router.get("/progress")
def get_player_progress(
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Return a complete progress snapshot for the authenticated player.

    Response shape:
      current   — live snapshot of rating, confidence, skill vector, and world-model
                  parameters (tier, teaching style, opponent ELO, explanation depth)
      history   — last 20 games with per-game weaknesses, rating, and confidence
      analysis  — HistoricalAnalysisPipeline output: category scores, phase rates,
                  dominant category, and prioritised training recommendations
    """
    # ── World-model snapshot ────────────────────────────────────────────────
    adaptation = compute_adaptation(float(player.rating), float(player.confidence))
    profile = adaptation["profile"]
    teaching = adaptation["teaching"]
    opponent = adaptation["opponent"]

    skill_vector = json.loads(player.skill_vector_json or "{}")
    tier = _TIER.get(teaching["style"], "intermediate")

    current = {
        "rating": float(player.rating),
        "confidence": float(player.confidence),
        "skill_vector": skill_vector,
        "tier": tier,
        "teaching_style": teaching["style"],
        "opponent_elo": opponent["target_elo"],
        "explanation_depth": round(profile.explanation_depth, 3),
        "concept_complexity": round(profile.concept_complexity, 3),
    }

    # ── Game history with per-game weaknesses ───────────────────────────────
    events = (
        db.query(GameEvent)
        .filter(GameEvent.player_id == player.id)
        .order_by(GameEvent.created_at.desc())
        .limit(20)
        .all()
    )

    history = []
    for ev in events:
        rating_update = (
            db.query(RatingUpdate).filter(RatingUpdate.event_id == str(ev.id)).first()
        )
        confidence_update = (
            db.query(ConfidenceUpdate).filter(ConfidenceUpdate.event_id == str(ev.id)).first()
        )
        weaknesses: dict = {}
        if ev.weaknesses_json:
            try:
                weaknesses = json.loads(ev.weaknesses_json)
            except Exception:
                pass
        history.append(
            {
                "game_id": str(ev.id),
                "result": ev.result,
                "accuracy": float(ev.accuracy),
                "rating_after": float(rating_update.rating_after) if rating_update else None,
                "confidence_after": (
                    float(confidence_update.confidence_after) if confidence_update else None
                ),
                "weaknesses": weaknesses,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            }
        )

    # ── Historical analysis ─────────────────────────────────────────────────
    analysis: dict = {
        "dominant_category": None,
        "games_analyzed": 0,
        "category_scores": {},
        "phase_rates": {},
        "recommendations": [],
    }
    recent_games = EventStorage(db).get_recent_games(player_id=str(player.id), limit=20)
    if recent_games:
        try:
            stats = HistoricalAnalysisPipeline(db).run(str(player.id), recent_games)
            analysis = {
                "dominant_category": stats.dominant_category,
                "games_analyzed": stats.games_analyzed,
                "category_scores": {k: round(v, 4) for k, v in stats.category_scores.items()},
                "phase_rates": {k: round(v, 4) for k, v in stats.phase_rates.items()},
                "recommendations": [
                    {"category": r.category, "priority": r.priority, "rationale": r.rationale}
                    for r in generate_training_recommendations(stats)
                ],
            }
        except Exception:
            logger.exception("HistoricalAnalysisPipeline failed in /player/progress")

    return {"current": current, "history": history, "analysis": analysis}
