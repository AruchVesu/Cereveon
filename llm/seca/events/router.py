import io
import json
import logging
import re

import chess
import chess.pgn

_PGN_HEADER_RE = re.compile(r'^\s*\[\s*\w+\s+"[^"]*"\s*\]', re.MULTILINE)
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, field_validator
from llm.seca.analysis.historical_pipeline import HistoricalAnalysisPipeline
from llm.seca.analytics.training_recommendations import generate_training_recommendations

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session as DBSession

from llm.seca.auth.router import get_db, get_current_player
from llm.seca.shared_limiter import limiter
from .storage import EventStorage
from llm.seca.skills.updater import SkillUpdater
from llm.seca.brain.models import RatingUpdate, ConfidenceUpdate
from llm.seca.coach.live_controller import (
    PostGameCoachController,
    GameSummary,
)
from llm.seca.events.models import GameEvent
from llm.seca.coach.executor import CoachExecutor
from types import SimpleNamespace
from llm.seca.runtime.safe_mode import SAFE_MODE

router = APIRouter(prefix="/game", tags=["game"])


# Code points that terminate or visually break a log line.  Stripped by
# _safe_log() so an attacker controlling a request body field cannot
# inject forged log lines (CWE-117).  Includes the obscure Unicode line
# separators that some loggers respect even though repr() normally
# escapes ASCII control characters.
_LOG_INJECTION_CHARS = ("\r", "\n", "\x85", " ", " ")


def _safe_log(value: object, max_len: int = 80) -> str:
    """Sanitize an untrusted value for safe inclusion in a log line.

    Mitigates CWE-117 log injection where a request-body field could
    otherwise embed CR/LF and forge a fake log entry.  ``repr()``
    escapes standard ASCII control characters, but Unicode line
    separators (NEL U+0085, U+2028, U+2029) can slip past it depending
    on the encoder; we strip every line-terminating code point, then
    truncate so a giant payload cannot bloat the log file.

    The explicit replace chain is also what makes the sanitiser visible
    to CodeQL's ``py/log-injection`` taint tracker — bare ``repr`` is
    not recognised as a sanitiser even though it is one.
    """
    s = repr(value)
    for ch in _LOG_INJECTION_CHARS:
        s = s.replace(ch, "")
    return s[:max_len]


# ---------------------------------------------------------------------------
# Bandit decision integration
# ---------------------------------------------------------------------------
#
# SECA v1's deferred step 2 — bandit-driven action selection.  This
# helper wraps the LinUCB head from seca/brain/bandit/decision.py
# behind a feature flag (SECA_USE_BANDIT_COACH).  Two modes:
#
#   Flag off (default): deterministic controller's action is what
#       the user sees; bandit observes the (context, action, reward)
#       tuple anyway so it can warm up.
#   Flag on:  bandit's UCB1 selection becomes user-visible; same
#       observation logging happens.
#
# The flag-off-with-observations design is the warm-up phase: by
# the time someone flips the flag in production, the bandit has
# already seen real reward signals from real games.
#
# Action space mirrors PostGameCoachController's rule outputs:
#   NONE / REFLECT / DRILL / PUZZLE / PLAN_UPDATE
# Bandit weights are stored per (player_id, action) in the
# `bandit_weights` table — see seca/brain/bandit/decision.py.

import os as _os

_BANDIT_ACTIONS = ("NONE", "REFLECT", "DRILL", "PUZZLE", "PLAN_UPDATE")


def _apply_bandit_decision(
    player_id: str,
    deterministic_action,
    rating_before: float,
    confidence_before: float,
    accuracy: float,
    weaknesses: dict,
    reward: float,
):
    """Observe the deterministic action's outcome via the bandit;
    optionally override the chosen action with the bandit's UCB1
    selection when SECA_USE_BANDIT_COACH=1.

    Bandit failures are non-fatal — if anything blows up we log and
    return the deterministic action unchanged so the /game/finish
    request keeps working.
    """
    use_bandit = _os.getenv("SECA_USE_BANDIT_COACH") == "1"

    try:
        from llm.seca.brain.bandit.context_builder import build_context_vector
        from llm.seca.brain.bandit import decision as bandit_decision

        context = build_context_vector(
            rating_before=rating_before,
            confidence_before=confidence_before,
            accuracy=accuracy,
            weaknesses=weaknesses,
        )

        # Always observe — even when the flag is off — so warm-up
        # accumulates from real games.
        try:
            bandit_decision.record_observation(
                player_id=player_id,
                context=context.tolist(),
                action=str(deterministic_action.type),
                reward=float(reward),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Bandit observation logging failed (non-fatal)")

        if not use_bandit:
            return deterministic_action

        # Flag on: UCB1 picks the next action.
        try:
            chosen_type = bandit_decision.select_action(
                player_id=player_id,
                context=context.tolist(),
                candidate_actions=_BANDIT_ACTIONS,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Bandit selection failed; falling back to deterministic")
            return deterministic_action

        # Reuse the deterministic action's `weakness` (it has the
        # game-specific context the bandit can't reconstruct).
        return SimpleNamespace(
            type=chosen_type,
            weakness=getattr(deterministic_action, "weakness", None),
            reason=f"bandit:linucb (warm={chosen_type == deterministic_action.type})",
        )
    except Exception:  # noqa: BLE001
        # Imports could fail if the freeze guard rejected the
        # decision module for any reason — keep the request alive.
        logger.exception("Bandit pipeline import failed (non-fatal)")
        return deterministic_action


class CoachFeedbackRequest(BaseModel):
    session_fen: str
    is_helpful: bool

    @field_validator("session_fen")
    @classmethod
    def validate_session_fen(cls, v: str) -> str:
        stripped = v.strip()
        if stripped.lower() == "startpos":
            return v
        parts = stripped.split()
        if len(parts) != 6 or len(stripped) > 100:
            raise ValueError("invalid FEN")
        try:
            chess.Board(stripped)
        except ValueError:
            raise ValueError("invalid FEN")
        return v


@router.post("/coach-feedback")
def coach_feedback(
    req: CoachFeedbackRequest,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    logger.info(
        "Coach feedback: player=%s fen=%s helpful=%s",
        player.id,
        _safe_log(req.session_fen, max_len=30),
        _safe_log(req.is_helpful, max_len=8),
    )
    return {"status": "recorded"}


class GameFinishRequest(BaseModel):
    pgn: str
    result: str  # win / loss / draw
    accuracy: float  # 0..1
    weaknesses: dict
    player_id: str | None = None
    # game_id ties this finish call back to the row created by the
    # corresponding /game/start request, so the `games` table row gets
    # its `result` / `finished_at` columns populated instead of sitting
    # in NULL purgatory forever.  Optional so older clients that don't
    # track the start-game response still work.  The Android Resume
    # flow reuses the same game_id across both the original session
    # and the resumed-with-the-same-position one, which is the whole
    # point — without this, every Resume would orphan the original
    # /game/start row.
    game_id: str | None = None

    @field_validator("pgn")
    @classmethod
    def validate_pgn(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("pgn must not be empty")
        if len(v) > 100_000:
            raise ValueError("pgn too large (max 100 000 chars)")
        if not _PGN_HEADER_RE.search(v):
            raise ValueError("invalid PGN: no PGN headers found")
        try:
            game = chess.pgn.read_game(io.StringIO(v))
        except Exception as exc:
            raise ValueError(f"invalid PGN: {exc}") from exc
        if game is None:
            raise ValueError("invalid PGN: no game found")
        if game.errors:
            raise ValueError(f"invalid PGN: {game.errors[0]}")
        return v

    @field_validator("result")
    @classmethod
    def validate_result(cls, v: str) -> str:
        if v not in {"win", "loss", "draw"}:
            raise ValueError("result must be 'win', 'loss', or 'draw'")
        return v

    @field_validator("accuracy")
    @classmethod
    def validate_accuracy(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("accuracy must be between 0.0 and 1.0")
        return v

    @field_validator("weaknesses")
    @classmethod
    def validate_weaknesses(cls, v: dict) -> dict:
        if len(v) > 50:
            raise ValueError("too many weakness entries (max 50)")
        for k, val in v.items():
            if not isinstance(k, str) or len(k) > 100:
                raise ValueError("weakness key must be a string ≤ 100 chars")
            # Reject C0 controls (0x00-0x1f) and DEL (0x7f).  Stored verbatim in
            # weaknesses_json and joined into log lines elsewhere; without this
            # check an attacker could plant a CRLF or null byte that surfaces
            # in downstream log/audit-tooling consumers.
            for ch in k:
                if ord(ch) < 0x20 or ord(ch) == 0x7f:
                    raise ValueError("weakness key contains control characters")
            if not isinstance(val, (int, float)):
                raise ValueError("weakness values must be numeric")
        return v

    @field_validator("game_id")
    @classmethod
    def validate_game_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Accept up to 64 chars (UUIDs are 36; auto-{uuid} keys are 41).
        # Anything longer is either malformed or an attempted overflow.
        if len(v) > 64:
            raise ValueError("game_id must be ≤ 64 chars")
        # The repo layer treats game_id as a SQL parameter (bound, not
        # interpolated), but reject control chars defensively for the
        # same reason we reject them in weakness keys — they end up in
        # log lines consumers may parse line-by-line.
        for ch in v:
            if ord(ch) < 0x20 or ord(ch) == 0x7f:
                raise ValueError("game_id contains control characters")
        return v


@router.post("/finish")
@limiter.limit("10/minute")
def finish_game(
    req: GameFinishRequest,
    request: Request,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    if req.player_id is not None and req.player_id != str(player.id):
        raise HTTPException(status_code=403, detail="Cannot submit game for another player")

    storage = EventStorage(db)

    rating_before = player.rating
    confidence_before = player.confidence

    event = storage.store_game(
        player_id=player.id,
        pgn=req.pgn,
        result=req.result,
        accuracy=req.accuracy,
        weaknesses=req.weaknesses,
    )

    # If the client tracked the game_id from /game/start, mark the
    # corresponding `games` row complete.  Best-effort: a missing /
    # stale game_id (the row was already finalised, deleted by a
    # cleanup job, or never created because /game/start failed) must
    # not fail the whole finish call — the GameEvent + skill update
    # already happened above and they're the load-bearing writes.
    if req.game_id:
        try:
            from llm.seca.storage.repo import finish_game as _repo_finish_game
            _repo_finish_game(req.game_id, req.result)
        except Exception:
            logger.exception(
                "repo.finish_game failed for game_id=%s; GameEvent already stored",
                _safe_log(req.game_id),
            )

    # ---- skill update ----
    try:
        SkillUpdater(db).update_from_event(player.id, event)
    except Exception:
        logger.exception(
            "SkillUpdater failed for player %s; rating not updated this game", player.id
        )
    db.refresh(player)

    rating_after = player.rating
    confidence_after = player.confidence
    reward = rating_after - rating_before
    rating_update = RatingUpdate(
        event_id=str(event.id),
        rating_before=float(rating_before),
        rating_after=float(rating_after),
    )
    confidence_update = ConfidenceUpdate(
        event_id=str(event.id),
        confidence_before=float(confidence_before),
        confidence_after=float(confidence_after),
    )
    db.add(rating_update)
    db.add(confidence_update)
    db.commit()

    if not SAFE_MODE:
        from llm.seca.brain.bandit.context_builder import build_context_vector

        context = build_context_vector(
            rating_before=rating_before,
            confidence_before=confidence_before,
            accuracy=req.accuracy,
            weaknesses=req.weaknesses,
        )

        try:
            from llm.seca.brain.bandit.online_update import update_after_game

            update_after_game(context, action_index=0, reward=reward)
            from llm.seca.brain.bandit.trainer import train_bandit
            from llm.seca.brain.neural_policy.train import train_policy

            train_bandit()
            train_policy()
        except Exception:
            logger.exception("Bandit update failed")

        try:
            from llm.seca.brain.planning.counterfactual import CounterfactualPlanner
            import numpy as np

            planner = CounterfactualPlanner()

            state = np.array([rating_after, confidence_after, req.accuracy])

            actions = [
                np.array([1, 0, 0]),  # tactics
                np.array([0, 1, 0]),  # openings
                np.array([0, 0, 1]),  # endgames
            ]

            idx, future, score = planner.choose_action(state, actions)

            logger.info("Chosen training: %s", idx)
            logger.info("Predicted rating/conf delta: %s", future)
            logger.info("Score: %s", score)
        except Exception:
            logger.exception("Counterfactual planner failed")

    controller = PostGameCoachController()

    game_summary = GameSummary(
        rating_before=rating_before,
        rating_after=rating_after,
        confidence_before=confidence_before,
        confidence_after=confidence_after,
        learning_delta=reward,
        weaknesses=req.weaknesses or {},
    )

    recent = (
        db.query(GameEvent)
        .filter(GameEvent.player_id == player.id)
        .order_by(GameEvent.created_at.desc())
        .limit(3)
        .all()
    )

    recent_weaknesses = []
    for ev in recent:
        if not ev.weaknesses_json:
            continue
        try:
            weaknesses = json.loads(ev.weaknesses_json)
            if isinstance(weaknesses, dict):
                recent_weaknesses.extend(list(weaknesses.keys()))
        except Exception:
            # Ignore malformed weakness payloads
            pass

    try:
        coach_action = controller.decide(
            game=game_summary,
            recent_weaknesses=recent_weaknesses,
        )

        # Bandit decision step — SECA v1's deferred milestone.  The
        # LinUCB head observes every game's outcome (warm-up); when
        # SECA_USE_BANDIT_COACH=1 it also overrides the action the
        # deterministic controller picked.  Off by default so live
        # behaviour is unchanged unless explicitly enabled.
        coach_action = _apply_bandit_decision(
            player_id=str(player.id),
            deterministic_action=coach_action,
            rating_before=rating_before,
            confidence_before=confidence_before,
            accuracy=req.accuracy,
            weaknesses=req.weaknesses or {},
            reward=reward,
        )

        executor = CoachExecutor()
        coach_content = executor.execute(coach_action)
    except Exception:
        logger.exception("Coach pipeline failed")
        coach_action = SimpleNamespace(type="ERROR", weakness=None, reason="coach_pipeline_error")
        coach_content = SimpleNamespace(
            title="Keep playing",
            description="No special training needed right now.",
            payload={},
        )

    if SAFE_MODE:
        learning_result = {"status": "safe_mode"}
    else:
        learner = request.app.state.seca_learner if request else None
        try:
            learning_result = learner.train_step() if learner else {"status": "no_learner"}
        except Exception:
            logger.exception("Learner train_step failed")
            learning_result = {"status": "learner_error"}

    # ---- historical analysis + training recommendations (deterministic, no RL) ----
    analysis_recommendations = []
    analysis_dominant_category = None
    analysis_games_analyzed = 0
    try:
        recent_games = storage.get_recent_games(player_id=str(player.id), limit=50)
        if recent_games:
            stats = HistoricalAnalysisPipeline(db).run(str(player.id), recent_games)
            analysis_dominant_category = stats.dominant_category
            analysis_games_analyzed = stats.games_analyzed
            analysis_recommendations = [
                {"category": r.category, "priority": r.priority, "rationale": r.rationale}
                for r in generate_training_recommendations(stats)
            ]
    except Exception:
        logger.exception("HistoricalAnalysisPipeline failed; recommendations omitted")

    return {
        "status": "stored",
        "new_rating": rating_after,
        "confidence": confidence_after,
        "learning": learning_result,
        "coach_action": {
            "type": coach_action.type,
            "weakness": coach_action.weakness,
            "reason": coach_action.reason,
        },
        "coach_content": {
            "title": coach_content.title,
            "description": coach_content.description,
            "payload": coach_content.payload,
        },
        "analysis": {
            "dominant_category": analysis_dominant_category,
            "games_analyzed": analysis_games_analyzed,
            "recommendations": analysis_recommendations,
        },
    }


@router.get("/history")
def game_history(
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    events = (
        db.query(GameEvent)
        .filter(GameEvent.player_id == player.id)
        .order_by(GameEvent.created_at.desc())
        .limit(20)
        .all()
    )
    games = []
    for ev in events:
        rating_update = (
            db.query(RatingUpdate)
            .filter(RatingUpdate.event_id == str(ev.id))
            .first()
        )
        games.append(
            {
                "id": str(ev.id),
                "result": ev.result,
                "accuracy": ev.accuracy,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
                "rating_after": float(rating_update.rating_after)
                if rating_update
                else None,
            }
        )
    return {"games": games}
