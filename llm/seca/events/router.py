import io
import json
import logging
import re

import chess
import chess.pgn

_PGN_HEADER_RE = re.compile(r'^\s*\[\s*\w+\s+"[^"]*"\s*\]', re.MULTILINE)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
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
from llm.seca.events.models import GameEvent, GameFinishResult
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
    engine_pool,
    req,  # GameFinishRequest, forward reference to avoid declaration order
    player_id: str,
) -> tuple[float, dict, str, "AccuracyAnalysis | None"]:
    """Recompute accuracy + weaknesses from the PGN via the engine pool.

    Returns a 4-tuple ``(accuracy, weaknesses, source, analysis)``
    where ``source`` is ``"engine"`` (recompute succeeded) or
    ``"client"`` (falling back to request fields) and ``analysis`` is
    the full ``AccuracyAnalysis`` when ``source == "engine"`` (None
    otherwise).  Downstream consumers — including the mistake-replay
    detector wired into the /game/finish response — read ``losses_cp``
    + ``player_color`` off ``analysis`` so the route doesn't have to
    re-walk the PGN itself.

    Emits an ``ACC_DIVERGENCE`` warning when the recomputed accuracy
    differs from the client value by at least
    ``_DIVERGENCE_WARN_THRESHOLD`` — anti-cheat telemetry that
    operators can grep for in production logs.

    Takes ``engine_pool`` directly rather than reading it off
    ``request.app.state``: the helper is now also called from a
    FastAPI BackgroundTask (the ``?async=true`` path), which has no
    access to the original ``Request``.  The sync handler reads the
    pool from ``request.app.state`` once and passes it in.
    """
    pool = engine_pool
    if pool is None:
        logger.info(
            "ACC_FALLBACK player=%s reason=engine_pool_unavailable",
            _safe_log(player_id),
        )
        return req.accuracy, req.weaknesses, "client", None

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
        return req.accuracy, req.weaknesses, "client", None

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
        return req.accuracy, req.weaknesses, "client", None

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

    return analysis.accuracy, analysis.weaknesses, "engine", analysis


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
                if ord(ch) < 0x20 or ord(ch) == 0x7F:
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
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError("game_id contains control characters")
        return v


@router.post("/finish")
@limiter.limit("10/minute")
def finish_game(
    req: GameFinishRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    if req.player_id is not None and req.player_id != str(player.id):
        raise HTTPException(status_code=403, detail="Cannot submit game for another player")

    # Attribute any LLM telemetry emitted under this request to the
    # game_id via the log_config contextvar (see llm/log_config.py).
    # ``req.game_id`` is optional on /game/finish — when absent (legacy
    # clients that finished without /game/start), the contextvar stays
    # unset and the llm_call log line records ``game_id: null``.
    from llm import log_config as _log_config

    game_id_token = _log_config.set_game_id(req.game_id) if req.game_id else None
    try:
        return _finish_game_body(req, request, background_tasks, player, db)
    finally:
        if game_id_token is not None:
            _log_config.game_id_var.reset(game_id_token)


def _finish_game_body(
    req: GameFinishRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    player,
    db: DBSession,
):

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
    accuracy, weaknesses, _, accuracy_analysis = _resolve_authoritative_accuracy(
        engine_pool=getattr(request.app.state, "engine_pool", None),
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
        # Link this history row back to the live game's chat thread so the
        # game-history UI can load its coaching conversation.  Already
        # validated + normalised (blank -> None) by GameFinishRequest.
        app_game_id=req.game_id,
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
    # If SkillUpdater raises, the SQLAlchemy session is left in an aborted
    # state on Postgres (any prior failed DDL/DML marks the txn unusable).
    # We MUST rollback before the next ORM call (``db.refresh(player)``) or
    # the handler cascades to a 500 even though the GameEvent already
    # committed above.  See the 2026-05-15 prod incident: a broken
    # ``CREATE TABLE … AUTOINCREMENT`` inside ExperienceStore.log aborted
    # the txn and ``db.refresh`` then threw InFailedSqlTransaction.
    try:
        SkillUpdater(db).update_from_event(player.id, event)
    except Exception:
        logger.exception(
            "SkillUpdater failed for player %s; rating not updated this game", player.id
        )
        db.rollback()
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

        # Pass the GameSummary so the executor's default handler can
        # synthesise a per-game description from engine-truth fields
        # (rating delta, per-phase weakness rates) instead of the
        # generic "Keep playing" stub.  See PR #173 + the 2026-05-16
        # on-device feedback — the dashboard had no useful coach copy
        # to render when the controller chose NONE.
        executor = CoachExecutor()
        coach_content = executor.execute(coach_action, game=game_summary)
    except Exception:
        logger.exception("Coach pipeline failed")
        coach_action = CoachAction(type="ERROR", weakness=None, reason="coach_pipeline_error")
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

    # Mistake-replay extraction (Phase 3).  Cheap piggy-back on the
    # accuracy recompute the engine pool already ran above — walk the
    # PGN to the player's FIRST move whose centipawn loss clears
    # ``MIN_MISTAKE_LOSS_CP`` and surface it on the response so the
    # Android replay sheet can launch.  Picks the first above-threshold
    # loss (not the largest) so the user learns the originating
    # mistake before its downstream cascade.  Skipped entirely when
    # the recompute fell back to client values (analysis is None) or
    # when no move clears the threshold; in both cases the response
    # carries ``biggest_mistake: null`` and the client doesn't show
    # the replay CTA.  The wire field name remains ``biggest_mistake``
    # for backward compatibility with the Android decoder; see
    # ``llm/seca/mistakes/detector.py`` module docstring for the
    # rationale.
    biggest_mistake_field: dict | None = None
    if accuracy_analysis is not None:
        try:
            from llm.seca.mistakes.detector import find_first_mistake

            mistake = find_first_mistake(
                pgn_text=req.pgn,
                losses_cp=accuracy_analysis.losses_cp,
                player_pov_eval_before_cp=accuracy_analysis.player_pov_eval_before_cp,
                player_pov_eval_after_cp=accuracy_analysis.player_pov_eval_after_cp,
                player_color=accuracy_analysis.player_color,
            )
            if mistake is not None:
                # source_ref ties the solve event to the specific
                # mistake position so /training/solve dedups correctly
                # if the user submits the same replay twice (e.g. a
                # flaky network retry).  Event id is the canonical
                # post-finish identifier — always populated, unlike
                # the optional client-supplied game_id.
                biggest_mistake_field = {
                    "fen": mistake.fen_before,
                    "played_move": mistake.played_uci,
                    "move_number": mistake.move_number,
                    "eval_loss_cp": mistake.eval_loss_cp,
                    "source_ref": (f"event_{event.id}:move_{mistake.move_number}"),
                }

                # Per-mistake study-plan agent (LLM coaching v1, phase 1
                # scaffold).  Background-task entrypoint runs AFTER the
                # response is sent — writes a 3-puzzle plan keyed off
                # this mistake.  Phase 1 stub: all 3 puzzles share the
                # mistake FEN, theme="generic", verdict="".  Phases
                # 2-4 light up the LLM verdict, the library variants,
                # and the Android Home card.  Lazy import to keep
                # /game/finish hot-path imports unchanged on requests
                # that don't carry a first-mistake.
                from llm.seca.coach.study_plan.agent import (
                    generate_plan_async,
                )

                background_tasks.add_task(
                    generate_plan_async,
                    player_id=str(player.id),
                    source_event_id=str(event.id),
                    mistake_fen=mistake.fen_before,
                    played_uci=mistake.played_uci,
                )
        except Exception:  # noqa: BLE001 — never 500 /game/finish
            # Detector is non-critical — log and continue without the
            # field.  /game/finish must stay green even if the mistake
            # extractor blows up on a malformed PGN that somehow
            # passed the accuracy recompute.
            logger.exception(
                "first-mistake extraction failed for player=%s",
                _safe_log(str(player.id)),
            )

    response = {
        "status": "stored",
        # ``event_id`` exposes the server-generated GameEvent.id on the
        # wire so clients can fetch the same payload via
        # ``GET /game/finish/{event_id}/status``.  Useful today for
        # retry-after-network-drop recovery (the GET endpoint reads
        # from the persisted ``game_finish_results`` row); foundation
        # for the future async-recompute shape where POST returns
        # immediately and GET polls until the background task lands
        # the result.  Distinct from the optional client-supplied
        # ``game_id`` (which references the ``games`` row from
        # /game/start) — ``event_id`` always references this
        # particular finish call's GameEvent.
        "event_id": str(event.id),
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
        # Always present on the wire — null when no player move
        # cleared MIN_MISTAKE_LOSS_CP or the recompute fell back to
        # client values.  Field name retained for backward compat with
        # the Android decoder (``BiggestMistakeDto``); selection
        # policy is "first above threshold", not "largest loss".  The
        # Android client checks ``biggest_mistake !== null`` to decide
        # whether to show the "Replay your mistake" CTA on
        # GameSummary.
        "biggest_mistake": biggest_mistake_field,
    }

    # Persist the response so ``GET /game/finish/{event_id}/status``
    # can return it later — covers client retry-after-network-drop
    # today and prepares the table for the future async-recompute
    # path (background worker will write here when the Stockfish
    # recompute lands off the hot path).  Best-effort: the GameEvent
    # + RatingUpdate writes above are the load-bearing commits;
    # losing the result row only loses the retry-recovery convenience.
    try:
        db.add(
            GameFinishResult(
                event_id=str(event.id),
                response_json=json.dumps(response),
            )
        )
        db.commit()
    except Exception:
        logger.exception(
            "Persisting GameFinishResult failed for event_id=%s; "
            "GET /game/finish/<id>/status will 404 for this event but "
            "POST response is unaffected",
            _safe_log(str(event.id)),
        )
        db.rollback()

    return response


# Cap the path-segment width that ``event_id`` can take so a malicious
# probe can't waste a DB round-trip on a 10 MB string.  GameEvent.id is a
# uuid4 hex (36 chars); 64 is the same defensive cap GameFinishRequest
# applies to game_id.
_EVENT_ID_MAX_LEN = 64


@router.get("/finish/{event_id}/status")
def game_finish_status(
    event_id: str,
    player=Depends(get_current_player),
    db: DBSession = Depends(get_db),
):
    """Return the persisted ``POST /game/finish`` response payload for
    ``event_id``.

    Callers today:
      * Client retry recovery — if a mobile network drops the POST
        response after the server committed, the client refetches via
        this endpoint instead of replaying the (expensive, Stockfish-
        bound) finish call.
      * Future async-recompute polling clients — when the recompute
        moves off the POST hot path, the GameEvent exists immediately
        but the result row lands later from the background task.
        The 202 + ``{status: "pending"}`` shape below is the contract
        those clients poll against.

    Authorisation: requires the same ``X-API-Key`` + JWT the rest of
    the SECA surface enforces, plus an explicit ownership check that
    the event belongs to the calling player.

    Status-code map:
      200 — result row exists; body is the stored response payload
      202 — event exists but result row hasn't landed yet.  Polling
            clients SHOULD retry after a backoff.  Indistinguishable
            from "legacy event whose result row never materialised"
            (e.g., a finish that completed before PR #195 shipped,
            or a finish whose best-effort persistence raised in
            ``finish_game``).  Both cases share the polling-client
            contract: keep retrying or give up after a budget; the
            server can't tell them apart and doesn't try to.
      400 — ``event_id`` longer than the defensive cap
      403 — event exists but the calling player isn't its owner
      404 — event doesn't exist
    """
    if len(event_id) > _EVENT_ID_MAX_LEN:
        # Distinct from 422 (Pydantic validation) — 400 makes it clear
        # the value WAS well-formed enough to reach the handler but
        # exceeded the defensive bound.
        raise HTTPException(status_code=400, detail="event_id too long")

    event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")

    if event.player_id != str(player.id):
        # 403 (not 404) so operators see the cross-player probe
        # signal in access logs.  The same convention is used by
        # finish_game's player_id check at the top of the POST
        # handler.
        raise HTTPException(
            status_code=403,
            detail="Cannot read game finish result for another player",
        )

    result_row = db.query(GameFinishResult).filter(GameFinishResult.event_id == event_id).first()
    if result_row is None:
        # Pending shape — the future async-recompute path will reach
        # this branch every time the background task hasn't written
        # its result row yet.  Today the same branch fires for
        # legacy events (finishes that completed before PR #195
        # shipped) and for finishes whose best-effort
        # ``GameFinishResult`` persistence raised inside
        # ``finish_game``.  All three cases share the polling-client
        # contract: the result is not available right now; retry.
        # 202 (not 404) is the polling-friendly status — clients
        # know to keep trying.
        return JSONResponse(
            status_code=202,
            content={"status": "pending", "event_id": event_id},
        )

    try:
        return json.loads(result_row.response_json)
    except (json.JSONDecodeError, TypeError):
        # Corrupted row — the persisted JSON should always be valid
        # because we constructed it via ``json.dumps`` in
        # ``finish_game``.  Surface as 500 so the alert path fires;
        # don't fall back silently because the response shape is a
        # client contract.
        logger.exception(
            "GameFinishResult.response_json failed to decode for event_id=%s",
            _safe_log(event_id),
        )
        raise HTTPException(
            status_code=500,
            detail="Stored finish result is unreadable",
        )


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
        rating_update = db.query(RatingUpdate).filter(RatingUpdate.event_id == str(ev.id)).first()
        games.append(
            {
                "id": str(ev.id),
                # The live ``games.id`` (== chat_turns.game_id) this finished
                # game maps to, so the client can fetch its coaching chat via
                # GET /chat/history?game_id=...  None for legacy / imported /
                # pre-game_id rows (no per-game chat to show).
                "game_id": ev.app_game_id,
                "result": ev.result,
                "accuracy": ev.accuracy,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
                "rating_after": float(rating_update.rating_after) if rating_update else None,
            }
        )
    return {"games": games}
