"""Orchestration for the post-game review job.

``start_review`` (request thread) validates eligibility and creates or
coalesces the :class:`~llm.seca.review.models.GameReview` row;
``run_review_job`` (worker thread) advances it through the stages:

    engine stage   — ``compute_accuracy_from_pgn`` over the stored PGN
                     (the same engine-truth recompute /game/finish and
                     the post-import pass use), then deterministic
                     moment selection.  Persists ``engine_json`` +
                     ``moments_json`` and flips to ``engine_done`` — the
                     client's Wave-2 checkpoint.
    LLM stage      — entitlement-gated (``import_analysis``, admitted
                     per game so retries never double-charge), then
                     ``writer.write_review_texts`` under the Mode-2
                     gates.  Persists ``llm_json`` and completes.

Stages are idempotent: a worker picking up a row that already carries
``engine_json`` skips straight to the LLM stage — which is also how the
"Try coach review again" button re-runs Wave 3 without re-paying the
engine work.

Session discipline mirrors ``import_service``: the worker opens its own
``_WorkerSession`` (``expire_on_commit=False``) because the request
session that created the row is long closed.

Trust boundary: only local engine output and deterministic stats feed
the row; the LLM's contribution is confined to ``llm_json`` texts that
passed the writer's gates.  ``Player.rating`` / ``confidence`` are
never mutated here.  The one write outside the review row is the
same-value backfill of ``GameEvent.accuracy`` / ``weaknesses_json`` for
rows the post-import analysis pass has not scored yet — identical
values, identical semantics, saves a duplicate engine pass.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Callable

import chess.pgn
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession, sessionmaker

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.seca.analysis.pgn_accuracy import AccuracyAnalysis, compute_accuracy_from_pgn
from llm.seca.auth.models import Player
from llm.seca.auth.router import engine
from llm.seca.coach.study_plan.verdict import skill_hint_for_rating
from llm.seca.engines.stockfish.pool import StockfishEnginePool
from llm.seca.entitlements import service as entitlements
from llm.seca.events.models import GameEvent

# Single source of truth for the provenance string — same import the
# review router uses; drift-proof against a future rename.
from llm.seca.lichess.import_service import PLATFORM_LICHESS
from llm.seca.review.models import (
    ANALYSIS_VERSION,
    GameReview,
    LLM_OUTCOME_FALLBACK,
    LLM_OUTCOME_FULL,
    LLM_OUTCOME_SKIPPED_ENTITLEMENT,
    REVIEW_STATUS_ACTIVE,
    REVIEW_STATUS_COMPLETE,
    REVIEW_STATUS_ENGINE_DONE,
    REVIEW_STATUS_FAILED,
    REVIEW_STATUS_QUEUED,
    REVIEW_STATUS_RUNNING,
)
from llm.seca.review.moments import (
    MIN_REVIEW_PLIES,
    REVIEW_MODE_STANDARD,
    band_for_player_cp,
    build_player_move_records,
    select_critical_moments,
)
from llm.seca.review.writer import (
    KEY_MOMENTS,
    KEY_OUTCOME,
    KEY_VERDICT,
    SOURCE_FALLBACK,
    fallback_moment_text,
    fallback_verdict,
    write_review_texts,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from llm.rag.llm.base import BaseLLM

logger = logging.getLogger(__name__)

# Same worker-session rationale as import_service._WorkerSession: the
# worker thread outlives the request session, and expire_on_commit=False
# keeps the row usable across the per-stage commits.
_WorkerSession = sessionmaker(bind=engine, expire_on_commit=False)

#: Per-position engine budget for the review recompute — pinned to the
#: /game/finish default so a reviewed game and a finished game are
#: scored with identical depth (same invariant the post-import pass
#: documents in ``analysis_service.LICHESS_ANALYSIS_MOVETIME_MS``).
REVIEW_MOVETIME_MS = 200

#: ``error_message`` truncation bound (hostile/pathological messages).
_ERROR_MESSAGE_MAX = 500

#: Default rating for skill-hint mapping when the Player row carries
#: none (freshly registered accounts before calibration).
_DEFAULT_RATING = 800.0


class ReviewEligibilityError(Exception):
    """Raised by ``start_review`` for games that cannot be reviewed.

    ``code`` is a stable machine-readable discriminator the router maps
    onto the HTTP error detail: ``not_lichess`` | ``too_short`` |
    ``no_pgn``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _count_plies(pgn_text: str) -> int:
    game = chess.pgn.read_game(io.StringIO(pgn_text or ""))
    if game is None:
        return 0
    return sum(1 for _ in game.mainline_moves())


def check_eligibility(event: GameEvent) -> None:
    """Raise :class:`ReviewEligibilityError` unless ``event`` is reviewable."""
    if (event.source or "app") != PLATFORM_LICHESS:
        raise ReviewEligibilityError(
            "not_lichess", "Reviews are available for imported Lichess games only"
        )
    plies = _count_plies(event.pgn)
    if plies < MIN_REVIEW_PLIES:
        raise ReviewEligibilityError(
            "too_short", "This game was too short for a review — try a longer game"
        )


def start_review(  # pylint: disable=too-many-return-statements
    db: DBSession,
    player: Player,
    event: GameEvent,
    *,
    dispatch: Callable[[str], object] | None = None,
) -> tuple[GameReview, bool]:
    """Create-or-coalesce the review row for ``event``.

    The ``too-many-return-statements`` suppression is deliberate: this
    is a flat state dispatch over the existing row's status — each
    branch returns its ``(row, dispatched)`` verdict where it is
    decided, the same fail-fast-chain structure the study-plan
    verdict's ``_parse_and_validate`` documents.  Folding the returns
    into an accumulator would obscure which state produced the answer.

    Returns ``(row, dispatched)`` — ``dispatched`` is True when this
    call queued worker work (fresh row, failed-row requeue, or an LLM
    Wave-3 retry), so the router can answer 202 vs 200.

    Concurrency: the ``(game_event_id, analysis_version)`` unique
    constraint is the race guard — a concurrent POST that loses the
    insert race lands in the ``IntegrityError`` branch and coalesces
    onto the winner's row.  No in-process lock is needed because, unlike
    the import path, there is no SQLite-visible multi-writer stream —
    one short INSERT per POST.
    """
    check_eligibility(event)

    existing = (
        db.query(GameReview)
        .filter(
            GameReview.game_event_id == event.id,
            GameReview.analysis_version == ANALYSIS_VERSION,
        )
        .first()
    )

    if existing is not None:
        if existing.status in REVIEW_STATUS_ACTIVE:
            return existing, False

        if existing.status == REVIEW_STATUS_FAILED:
            # Engine stage failed (pool saturation, restart sweep, …) —
            # requeue the same row; the unique constraint forbids a
            # second one.
            existing.status = REVIEW_STATUS_QUEUED
            existing.error_message = None
            db.commit()
            if dispatch is not None:
                dispatch(existing.id)
            return existing, True

        # Terminal complete.  A full LLM outcome is served as-is;
        # fallback / entitlement-skipped outcomes are the "Try coach
        # review again" surface — re-run Wave 3 only.
        outcome = _llm_outcome(existing)
        if outcome == LLM_OUTCOME_FULL:
            return existing, False
        if outcome == LLM_OUTCOME_SKIPPED_ENTITLEMENT:
            # Don't churn the row while the cap is still in force —
            # admit() is idempotent per subject, so a blocked check here
            # stays blocked in the worker too.
            decision = entitlements.check(db, player, entitlements.METRIC_IMPORT_ANALYSIS)
            if not decision.allowed:
                return existing, False
        existing.status = REVIEW_STATUS_ENGINE_DONE
        db.commit()
        if dispatch is not None:
            dispatch(existing.id)
        return existing, True

    review = GameReview(
        game_event_id=event.id,
        player_id=str(player.id),
        status=REVIEW_STATUS_QUEUED,
        analysis_version=ANALYSIS_VERSION,
    )
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent POST won the insert race — coalesce onto its row.
        db.rollback()
        winner = (
            db.query(GameReview)
            .filter(
                GameReview.game_event_id == event.id,
                GameReview.analysis_version == ANALYSIS_VERSION,
            )
            .first()
        )
        if winner is not None:
            return winner, False
        raise  # constraint fired but no row visible — genuine bug, surface it
    if dispatch is not None:
        dispatch(review.id)
    return review, True


def _llm_outcome(review: GameReview) -> str | None:
    if not review.llm_json:
        return None
    try:
        payload = json.loads(review.llm_json)
    except (json.JSONDecodeError, TypeError):
        return None
    outcome = payload.get(KEY_OUTCOME)
    return outcome if isinstance(outcome, str) else None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def run_review_job(
    review_id: str,
    *,
    engine_pool: StockfishEnginePool | None,
    llm: "BaseLLM | None" = None,
) -> None:
    """Worker entrypoint — runs in the review thread pool, no request.

    ``llm`` accepts any ``BaseLLM`` for tests; None constructs the
    production ``DeepseekLLM`` lazily (only when the LLM stage is
    actually reached and admitted — a skipped stage never imports the
    adapter).
    """
    db = _WorkerSession()
    try:
        review = db.get(GameReview, review_id)
        if review is None:
            logger.error("review job %s not found in worker; aborting", review_id)
            return
        if review.status == REVIEW_STATUS_FAILED:
            # Startup janitor or an unlink raced ahead — don't clobber.
            logger.info("review job %s already failed; worker skipping", review_id)
            return

        event = db.get(GameEvent, review.game_event_id)
        player = db.get(Player, review.player_id)
        if event is None or player is None:
            _fail(db, review, "game or player row missing at worker start")
            return

        if review.engine_json is None:
            if not _run_engine_stage(db, review, event, engine_pool):
                return

        _run_llm_stage(db, review, event, player, engine_pool, llm)

        review.status = REVIEW_STATUS_COMPLETE
        review.completed_at = datetime.utcnow()
        db.commit()
        logger.info(
            "review %s complete (event=%s outcome=%s)",
            review.id,
            review.game_event_id,
            _llm_outcome(review),
        )
    except Exception as exc:  # noqa: BLE001 — worker must never propagate
        logger.exception("review job %s failed", review_id)
        try:
            db.rollback()
            review = db.get(GameReview, review_id)
            if review is not None and review.status != REVIEW_STATUS_COMPLETE:
                _fail(db, review, str(exc))
        except Exception:  # noqa: BLE001 — best-effort failure marking
            logger.exception("review job %s could not be marked failed", review_id)
    finally:
        db.close()


def _fail(db: DBSession, review: GameReview, message: str) -> None:
    review.status = REVIEW_STATUS_FAILED
    review.error_message = (message or "unknown error")[:_ERROR_MESSAGE_MAX]
    db.commit()


def _run_engine_stage(
    db: DBSession,
    review: GameReview,
    event: GameEvent,
    engine_pool: StockfishEnginePool | None,
) -> bool:
    """Engine recompute + moment selection.  True = proceed to LLM stage."""
    review.status = REVIEW_STATUS_RUNNING
    db.commit()

    if engine_pool is None:
        _fail(db, review, "engine pool unavailable")
        return False

    try:
        analysis = compute_accuracy_from_pgn(
            event.pgn,
            engine_pool,
            result=event.result,
            movetime_ms=REVIEW_MOVETIME_MS,
        )
    except (RuntimeError, ValueError) as exc:
        _fail(db, review, f"engine analysis failed: {exc}")
        return False

    if analysis.source != "engine" or not analysis.white_pov_eval_per_position_cp:
        _fail(db, review, "game could not be analyzed from its PGN")
        return False

    player_is_white = _player_is_white(event, analysis)
    records = build_player_move_records(
        event.pgn,
        analysis.white_pov_eval_per_position_cp,
        player_is_white=player_is_white,
    )
    moments, mode = select_critical_moments(records)

    review.engine_json = json.dumps(_engine_payload(analysis, event, player_is_white))
    review.moments_json = json.dumps([m.to_payload() for m in moments])
    review.review_mode = mode
    review.status = REVIEW_STATUS_ENGINE_DONE
    db.commit()

    _backfill_event_score(db, event, analysis)
    return True


def _player_is_white(event: GameEvent, analysis: AccuracyAnalysis) -> bool:
    """Imported rows carry ``player_color``; fall back to the accuracy
    recompute's Result-tag inference for legacy rows."""
    if event.player_color in ("white", "black"):
        return event.player_color == "white"
    return bool(analysis.player_color)  # chess.WHITE is truthy (True)


def _engine_payload(
    analysis: AccuracyAnalysis,
    event: GameEvent,
    player_is_white: bool,
) -> dict:
    """Wave-2 payload.  Wire-safe: the eval series is BANDED (the five
    player-relative Atrium steps) — raw centipawns never leave the
    process, enforcing the no-numeric-eval invariant at the wire."""
    sign = 1 if player_is_white else -1
    bands = [band_for_player_cp(sign * cp) for cp in analysis.white_pov_eval_per_position_cp]
    return {
        "bands": bands,
        "accuracy": round(float(analysis.accuracy), 4),
        "counts": {
            "blunders": analysis.blunder_count,
            "mistakes": analysis.mistake_count,
            "inaccuracies": analysis.inaccuracy_count,
        },
        "moves_analyzed": analysis.moves_analyzed,
        "player_color": "white" if player_is_white else "black",
        "plies": max(0, len(analysis.white_pov_eval_per_position_cp) - 1),
        "meta": _pgn_meta(event.pgn),
    }


#: PGN headers echoed into ``engine_json.meta`` for the Wave-1 header.
#: Lichess exports carry Opening/ECO, giving the spec's "opening ID"
#: for free.  Values are pass-through strings, absent when the header
#: is absent.
_META_HEADERS = {
    "White": "white",
    "Black": "black",
    "WhiteElo": "white_elo",
    "BlackElo": "black_elo",
    "TimeControl": "time_control",
    "Opening": "opening",
    "ECO": "eco",
    "UTCDate": "date",
    "Termination": "termination",
}


def _pgn_meta(pgn_text: str) -> dict:
    game = chess.pgn.read_game(io.StringIO(pgn_text or ""))
    if game is None:
        return {}
    meta: dict[str, str] = {}
    for header, key in _META_HEADERS.items():
        value = game.headers.get(header)
        if value and value != "?":
            meta[key] = str(value)[:200]
    return meta


def _backfill_event_score(db: DBSession, event: GameEvent, analysis: AccuracyAnalysis) -> None:
    """Write accuracy/weaknesses onto a not-yet-scored imported row.

    Same values, same semantics as ``analysis_service.analyze_unscored_games``
    — the review already paid for the engine pass, so the post-import
    pass should not redo it.  Scored rows are never touched.
    """
    try:
        if event.accuracy is None or event.accuracy == 0.0:
            event.accuracy = float(analysis.accuracy)
            event.weaknesses_json = json.dumps(analysis.weaknesses)
            db.commit()
    except Exception:  # noqa: BLE001 — enrichment only; the review must not fail on it
        logger.exception("review: event score backfill failed for %s", event.id)
        db.rollback()


def _run_llm_stage(
    db: DBSession,
    review: GameReview,
    event: GameEvent,
    player: Player,
    engine_pool: StockfishEnginePool | None,
    llm: "BaseLLM | None",
) -> None:
    """Wave 3 — entitlement-gated coach texts.  Never raises upward."""
    decision = entitlements.admit(
        db,
        player,
        entitlements.METRIC_IMPORT_ANALYSIS,
        subject=str(review.game_event_id),
    )
    if not decision.allowed:
        review.llm_json = json.dumps({KEY_OUTCOME: LLM_OUTCOME_SKIPPED_ENTITLEMENT})
        db.commit()
        logger.info(
            "review %s LLM stage skipped: entitlement (plan=%s remaining=%s)",
            review.id,
            decision.plan,
            decision.remaining,
        )
        return

    try:
        moments_payload = json.loads(review.moments_json or "[]")
    except (json.JSONDecodeError, TypeError):
        moments_payload = []

    if llm is None:
        # Lazy — a skipped stage (above) never imports the adapter.
        from llm.rag.llm.deepseek import DeepseekLLM  # noqa: PLC0415

        llm = DeepseekLLM()

    engine_signals: dict[int, dict] = {}
    for moment in moments_payload:
        fen = moment.get("fen_before")
        ply = int(moment.get("ply", 0))
        stockfish_json: dict = {}
        if engine_pool is not None and fen:
            try:
                # Cache-warm from the engine stage moments ago; a miss
                # costs one bounded 200ms probe per moment (≤ 3).
                stockfish_json = engine_pool.evaluate_position(
                    fen=fen, movetime_ms=REVIEW_MOVETIME_MS
                )
            except Exception:  # noqa: BLE001 — flags are enrichment, not correctness
                logger.warning("review %s: ESV probe failed for ply %s", review.id, ply)
        engine_signals[ply] = extract_engine_signal(stockfish_json, fen=fen)

    engine_payload = json.loads(review.engine_json or "{}")
    texts = write_review_texts(
        llm=llm,
        moments=moments_payload,
        engine_signals=engine_signals,
        result=(event.result or "draw").lower(),
        review_mode=review.review_mode or REVIEW_MODE_STANDARD,
        skill_hint=skill_hint_for_rating(float(player.rating or _DEFAULT_RATING)),
        accuracy_phrase=_accuracy_phrase(float(engine_payload.get("accuracy", 0.5))),
        weak_phases=_weak_phases(event),
    )
    review.llm_json = json.dumps(texts)
    db.commit()


def _accuracy_phrase(accuracy: float) -> str:
    """Qualitative overall-play phrase — never a number on the wire."""
    if accuracy >= 0.8:
        return "very steady, with few real slips"
    if accuracy >= 0.6:
        return "steady, with a few sharp slips"
    if accuracy >= 0.4:
        return "uneven, with several sharp swings"
    return "stormy, with big swings both ways"


def _weak_phases(event: GameEvent) -> list[str]:
    """Phases with recorded weakness, heaviest first, from the event row."""
    try:
        weaknesses = json.loads(event.weaknesses_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(weaknesses, dict):
        return []
    ranked = sorted(
        ((k, v) for k, v in weaknesses.items() if isinstance(v, (int, float)) and v > 0),
        key=lambda kv: -kv[1],
    )
    return [k for k, _ in ranked]


# ---------------------------------------------------------------------------
# Serialization + janitor
# ---------------------------------------------------------------------------


def serialize_review(review: GameReview, *, entitlement: dict | None = None) -> dict:
    """Wire shape shared by POST (200/202) and GET.

    JSON columns are parsed so the client decodes one nested object;
    a corrupt column serializes as null rather than 500ing the poll."""

    def _parse(raw: str | None) -> object | None:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("review %s: corrupt JSON column while serializing", review.id)
            return None

    return {
        "review_id": review.id,
        "event_id": review.game_event_id,
        "status": review.status,
        "analysis_version": review.analysis_version,
        "review_mode": review.review_mode,
        "engine": _parse(review.engine_json),
        "moments": _parse(review.moments_json),
        "llm": _parse(review.llm_json),
        "error_message": review.error_message,
        "entitlement": entitlement,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "completed_at": review.completed_at.isoformat() if review.completed_at else None,
    }


def entitlement_summary(db: DBSession, player: Player) -> dict:
    """Non-consuming quota snapshot for the client's CTA copy."""
    decision = entitlements.check(db, player, entitlements.METRIC_IMPORT_ANALYSIS)
    return {
        "metric": entitlements.METRIC_IMPORT_ANALYSIS,
        "allowed": decision.allowed,
        "plan": decision.plan,
        "limit": decision.limit,
        "used": decision.used,
        "remaining": decision.remaining,
    }


def cleanup_stale_reviews_on_startup() -> int:
    """Sweep worker-less rows on process boot (lifespan startup).

    ``queued`` / ``running`` rows have no renderable content — swept to
    ``failed`` (the POST retry path requeues them on demand).
    ``engine_done`` rows DO have renderable Wave-2 content, so instead
    of discarding it they are completed with deterministic fallback
    texts — the same safety floor the writer serves when the provider
    is down; the "Try coach review again" path stays available because
    the outcome is ``fallback``.
    """
    db = _WorkerSession()
    try:
        swept = (
            db.query(GameReview)
            .filter(GameReview.status.in_((REVIEW_STATUS_QUEUED, REVIEW_STATUS_RUNNING)))
            .update(
                {
                    GameReview.status: REVIEW_STATUS_FAILED,
                    GameReview.error_message: "abandoned by server restart",
                },
                synchronize_session=False,
            )
        )
        db.commit()

        stranded = db.query(GameReview).filter(GameReview.status == REVIEW_STATUS_ENGINE_DONE).all()
        for review in stranded:
            try:
                moments_payload = json.loads(review.moments_json or "[]")
            except (json.JSONDecodeError, TypeError):
                moments_payload = []
            event = db.get(GameEvent, review.game_event_id)
            result = (event.result or "draw").lower() if event is not None else "draw"
            review.llm_json = json.dumps(
                {
                    KEY_MOMENTS: [
                        {
                            "ply": int(m.get("ply", 0)),
                            "text": fallback_moment_text(m),
                            "source": SOURCE_FALLBACK,
                        }
                        for m in moments_payload
                    ],
                    KEY_VERDICT: {
                        "text": fallback_verdict(
                            result=result,
                            review_mode=review.review_mode or REVIEW_MODE_STANDARD,
                            weak_phases=[],
                        ),
                        "source": SOURCE_FALLBACK,
                    },
                    KEY_OUTCOME: LLM_OUTCOME_FALLBACK,
                }
            )
            review.status = REVIEW_STATUS_COMPLETE
            review.completed_at = datetime.utcnow()
        db.commit()

        total = int(swept) + len(stranded)
        if total:
            logger.info(
                "review janitor: %d queued/running failed, %d engine_done completed with fallbacks",
                swept,
                len(stranded),
            )
        return total
    finally:
        db.close()
