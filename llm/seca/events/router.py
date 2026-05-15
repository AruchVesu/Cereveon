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
    CoachAction,
    PostGameCoachController,
    GameSummary,
)
from llm.seca.events.models import GameEvent
from llm.seca.coach.executor import CoachContent, CoachExecutor
from llm.seca.analysis.pgn_accuracy import (
    AccuracyAnalysis,
    compute_accuracy_from_pgn,
)

router = APIRouter(prefix="/game", tags=["game"])


# Code points that terminate or visually break a log line.  Stripped by
# _safe_log() so an attacker controlling a request body field cannot
# inject forged log lines (CWE-117).  Includes the obscure Unicode line
# separators that some loggers respect even though repr() normally
# escapes ASCII control characters.
_LOG_INJECTION_CHARS = ("\r", "\n", "\x85", " ", " ")


_LOG_INJECTION_RE = re.compile("[" + "".join(_LOG_INJECTION_CHARS) + "]")


def _safe_log(value: object, max_len: int = 80) -> str:
    """Sanitize an untrusted value for safe inclusion in a log line.

    Mitigates CWE-117 log injection where a request-body field could
    otherwise embed CR/LF and forge a fake log entry.  ``repr()``
    escapes standard ASCII control characters, but Unicode line
    separators (NEL U+0085, U+2028, U+2029) can slip past it depending
    on the encoder; we strip every line-terminating code point with a
    regex pass, then truncate so a giant payload cannot bloat the log.

    Implementation note: ``re.sub`` is deliberate.  CodeQL's
    ``py/log-injection`` taint tracker recognises ``re.sub(pattern,
    "", str)`` as a sanitiser but does NOT recognise the equivalent
    loop-of-``str.replace`` shape (CodeQL alerts #229–#230, fixed
    2026-05-13).
    """
    return _LOG_INJECTION_RE.sub("", repr(value))[:max_len]


# ---------------------------------------------------------------------------
# Server-side accuracy + weakness recompute
# ---------------------------------------------------------------------------
#
# /game/finish previously trusted the client's self-reported ``accuracy``
# and ``weaknesses`` on faith — a modded Android client could send
# ``accuracy=1.0, weaknesses={}`` and poison the bandit's context
# vector + the player's rating delta.  The trust gap was documented in
# ``docs/SECA.md`` under "Trust property of the reward signal".
#
# The helper below re-analyses the submitted PGN with the engine pool
# and replaces the client's accuracy / weaknesses with engine-truth
# values.  Falls back to client values when the engine pool is
# unavailable (Stockfish missing, pool saturated, or analysis raised)
# so the route's success path is preserved; the ``ACC_FALLBACK`` /
# ``ACC_DIVERGENCE`` log signals surface degraded coverage to
# operators.
#
# The recompute itself lives in
# ``llm.seca.analysis.pgn_accuracy.compute_accuracy_from_pgn``; this
# wrapper handles the engine-pool plumbing and the comparison logging.

_DIVERGENCE_WARN_THRESHOLD = 0.20


def _resolve_authoritative_accuracy(
    *,
    request: Request,
    req,  # GameFinishRequest, forward reference to avoid declaration order
    player_id: str,
) -> tuple[float, dict, str]:
    """Recompute accuracy + weaknesses from the PGN via the engine pool.

    Returns a 3-tuple ``(accuracy, weaknesses, source)`` where source
    is ``"engine"`` (recompute succeeded) or ``"client"`` (falling
    back to request fields).  Emits an ``ACC_DIVERGENCE`` warning
    when the recomputed accuracy differs from the client value by
    at least ``_DIVERGENCE_WARN_THRESHOLD`` — anti-cheat telemetry
    that operators can grep for in production logs.
    """
    pool = getattr(request.app.state, "engine_pool", None)
    if pool is None:
        logger.info(
            "ACC_FALLBACK player=%s reason=engine_pool_unavailable",
            _safe_log(player_id),
        )
        return req.accuracy, req.weaknesses, "client"

    try:
        analysis: AccuracyAnalysis = compute_accuracy_from_pgn(
            pgn_text=req.pgn,
            engine_pool=pool,
            result=req.result,
        )
    except (ValueError, RuntimeError):
        logger.exception(
            "ACC_FALLBACK player=%s reason=recompute_failed",
            _safe_log(player_id),
        )
        return req.accuracy, req.weaknesses, "client"

    if analysis.moves_analyzed == 0:
        # The engine pool was available AND the recompute ran, but the
        # PGN produced zero player moves.  Combined with a non-trivial
        # ``result`` ("win" / "loss"), this is a contradiction — the
        # player can't win or lose a game they didn't move in.  Reject
        # rather than fall back: the 2-engine-acquire cost would
        # otherwise buy a modded client a bypass of the trust gap.
        # Distinct from the ``pool is None`` branch above, which IS
        # a legitimate fallback (Stockfish unavailable).
        logger.warning(
            "ACC_REJECT player=%s reason=zero_player_moves moves_in_pgn=%d",
            _safe_log(player_id),
            analysis.moves_analyzed,
        )
        raise HTTPException(
            status_code=422,
            detail="PGN has no player moves; result/PGN combination is invalid.",
        )
    if analysis.source != "engine":
        logger.info(
            "ACC_FALLBACK player=%s reason=non_engine_source",
            _safe_log(player_id),
        )
        return req.accuracy, req.weaknesses, "client"

    divergence = abs(analysis.accuracy - float(req.accuracy))
    if divergence >= _DIVERGENCE_WARN_THRESHOLD:
        logger.warning(
            "ACC_DIVERGENCE player=%s client=%.3f server=%.3f delta=%.3f moves=%d",
            _safe_log(player_id),
            float(req.accuracy),
            analysis.accuracy,
            divergence,
            analysis.moves_analyzed,
        )

    return analysis.accuracy, analysis.weaknesses, "engine"


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
        return CoachAction(
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
    db: DBSession = Depends(get_db),  # pylint: disable=unused-argument
):
    # ``db`` is injected for symmetry with the other authenticated routes
    # (and so a future revision that persists feedback rows has the
    # session ready) — pylint flags it as unused for now.
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
        # Reject PGNs whose mainline parses to zero moves.  ``read_game``
        # is tolerant of unrecognized SAN tokens (drops them from the
        # mainline rather than populating ``game.errors``), so a body
        # like ``1. e4 e9 1-0`` parses to a 1-move mainline and an
        # empty body parses to zero moves — both should fail at the
        # Pydantic boundary rather than slip through to the server-
        # side accuracy recompute as a bypass surface.
        if not list(game.mainline_moves()):
            raise ValueError("invalid PGN: no moves found in mainline")
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
        if not 0.0 <= v <= 1.0:
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

    # Server-side accuracy + weakness recompute.  Closes the client-trust
    # gap on /game/finish; falls back to the request fields when the
    # engine pool is unavailable.  Every downstream consumer (event
    # storage, skill update, bandit context) reads from these locals
    # rather than from req.accuracy / req.weaknesses so a single
    # decision point gates whether trust flows from the client or from
    # engine truth.
    # The source ("engine" vs "client") is surfaced through log signals
    # in the resolver (``ACC_FALLBACK`` / ``ACC_DIVERGENCE``); we don't
    # propagate it onto the response or stored record yet — that's a
    # follow-up if operators want a queryable telemetry column.
    accuracy, weaknesses, _ = _resolve_authoritative_accuracy(
        request=request,
        req=req,
        player_id=str(player.id),
    )

    storage = EventStorage(db)

    rating_before = player.rating
    confidence_before = player.confidence

    event = storage.store_game(
        player_id=player.id,
        pgn=req.pgn,
        result=req.result,
        accuracy=accuracy,
        weaknesses=weaknesses,
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

    # Sprint 6.A follow-up (2026-05-12): the ``if not SAFE_MODE:`` block
    # that previously lived here imported four adaptive-learning modules
    # forbidden by the SECA freeze guard:
    #   - llm.seca.brain.bandit.online_update     (FORBIDDEN_MODULE_PARTS)
    #   - llm.seca.brain.bandit.trainer           (not in ALLOWED_BRAIN_MODULES)
    #   - llm.seca.brain.neural_policy.train      (not in ALLOWED_BRAIN_MODULES)
    #   - llm.seca.brain.planning.counterfactual  (not in ALLOWED_BRAIN_MODULES)
    #
    # The freeze guard crashes the process on import of any of these in
    # the live runtime, so the block was unreachable by construction —
    # SAFE_MODE is True in every supported deployment (prod sets
    # SECA_ENV=prod which the guard requires; dev keeps SAFE_MODE True
    # by default and crashes with SECA_ENABLE_ONLINE_LEARNING=1).
    # Deleted both for hygiene (the dead-import surface was a perennial
    # mypy/freeze tripwire) and because keeping it could mask a real
    # safety regression: a future contributor flipping SAFE_MODE
    # off-policy would never see the runtime crash they'd otherwise get,
    # because the path no longer exists to fail.

    controller = PostGameCoachController()

    game_summary = GameSummary(
        rating_before=rating_before,
        rating_after=rating_after,
        confidence_before=confidence_before,
        confidence_after=confidence_after,
        learning_delta=reward,
        weaknesses=weaknesses or {},
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
            # Loop-local name — must NOT shadow the outer authoritative
            # ``weaknesses`` (resolver output) consumed by
            # _apply_bandit_decision below.  A 2026-05-14 reviewer pass
            # caught this exact shadow regressing the bandit context
            # vector to recent[-1].weaknesses_json (i.e., a prior game's
            # client-supplied weaknesses).  Pinned by
            # test_pgn_accuracy.test_recent_weakness_loop_does_not_shadow.
            parsed = json.loads(ev.weaknesses_json)
            if isinstance(parsed, dict):
                recent_weaknesses.extend(list(parsed.keys()))
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
            accuracy=accuracy,
            weaknesses=weaknesses or {},
            reward=reward,
        )

        executor = CoachExecutor()
        coach_content = executor.execute(coach_action)
    except Exception:
        logger.exception("Coach pipeline failed")
        coach_action = CoachAction(
            type="ERROR", weakness=None, reason="coach_pipeline_error"
        )
        coach_content = CoachContent(
            title="Keep playing",
            description="No special training needed right now.",
            payload={},
        )

    # SAFE_MODE is True in every supported deployment (prod sets
    # SECA_ENV=prod which the freeze guard requires; dev keeps
    # SAFE_MODE True by default and crashes with
    # SECA_ENABLE_ONLINE_LEARNING=1).  No code path sets
    # ``request.app.state.seca_learner``, so the pre-PR-20 ``else``
    # branch was unreachable twice over.  Retired here to remove the
    # appearance of a live online-learning seam; revival path is
    # documented in [[project-seca-freeze-policy]].
    learning_result = {"status": "safe_mode"}

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
