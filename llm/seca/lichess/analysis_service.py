"""Post-import engine analysis for Lichess games.

Imported ``source='lichess'`` GameEvent rows land with an empty weakness
vector, which makes them invisible to ``HistoricalAnalysisPipeline`` (the
aggregator behind the curriculum, weakness charts, and progress
dashboard).  This module closes that gap: ``analyze_unscored_games`` runs
``compute_accuracy_from_pgn`` — the same engine-truth recompute
``/game/finish`` uses — over the player's unscored imported games and
writes ``accuracy`` + phase-keyed ``weaknesses_json`` back onto each row.

Called from the v2 import worker (``import_service.run_import_job``)
after the game stream completes; ``import_service`` imports THIS module
lazily inside the worker so no import cycle exists in either direction.

Trust boundary: only LOCAL engine output enters the rows (the import
stream pins ``evals=false`` upstream); player rating / confidence are
never mutated here — the SkillUpdater exclusion documented in
``import_service`` holds.
"""

from __future__ import annotations

import json
import logging
import os

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from llm.seca.analysis.pgn_accuracy import compute_accuracy_from_pgn
from llm.seca.auth.models import Player
from llm.seca.engines.stockfish.pool import StockfishEnginePool
from llm.seca.events.models import GameEvent
from llm.seca.lichess.models import JOB_STATUS_ACTIVE, LichessImportJob

logger = logging.getLogger(__name__)

# Mirrors import_service.PLATFORM_LICHESS.  Defined locally (not
# imported) so this module stays import-cycle-free with import_service,
# which lazily imports us from inside run_import_job.  Drift is pinned
# by test_lichess_analysis.py::test_platform_constant_matches.
_PLATFORM_LICHESS = "lichess"

# Games engine-analysed per import job (newest-first).  Bounds the
# engine-pool minutes one job can consume: at the /game/finish-parity
# 200 ms/ply budget a typical 80-ply game costs ~16 s, so the default
# cap is ~5 min of background engine time per job — documented in
# docs/THREAT_MODEL.md § T3.  Backlog beyond the cap is picked up by
# subsequent import jobs (the unscored predicate is stable).
LICHESS_ANALYSIS_MAX_GAMES = int(os.getenv("LICHESS_ANALYSIS_MAX_GAMES", "20"))

# Per-ply engine budget.  Defaults to the same 200 ms /game/finish uses
# (see llm/seca/analysis/pgn_accuracy.py "Performance notes") so
# imported and in-app games are scored with identical depth — halving it
# would re-open the slow-burn-mistake blindspot the 2026-05-20 bump
# closed.
LICHESS_ANALYSIS_MOVETIME_MS = int(os.getenv("LICHESS_ANALYSIS_MOVETIME_MS", "200"))


def _unscored_filter():
    """SQLAlchemy criteria matching imported rows the pass has not scored.

    The import stream constructs rows with ``accuracy=None``, but the
    column's Python-side default (``default=0.0`` on
    ``GameEvent.accuracy``) fires even for an explicit ``None`` kwarg at
    flush time, so imported rows actually persist ``0.0`` — and legacy
    rows written before that default behaviour was understood may hold
    NULL.  Both forms mark "unscored".  Scored rows can never collide:
    the ACPL→accuracy mapping ``1/(1+acpl/100)`` is strictly positive,
    and the degenerate-game fallback writes ``0.5``.
    """
    return or_(GameEvent.accuracy.is_(None), GameEvent.accuracy == 0.0)


def analyze_unscored_games(
    db: DBSession,
    player: Player,
    engine_pool: StockfishEnginePool,
    *,
    job: LichessImportJob | None = None,
    max_games_analyzed: int | None = None,
) -> int:
    """Engine-analyse the player's unscored imported games (bounded).

    Design properties:

    * BOUNDED — at most ``max_games_analyzed`` rows per call (default
      ``LICHESS_ANALYSIS_MAX_GAMES``); older backlog waits for the next
      import job.  Ordering is insertion recency (``created_at DESC``):
      the most recently imported batch is scored before older backlog.
      Within a single batch Lichess streamed newest-game-first, so
      insertion order runs newest→oldest and ``created_at DESC`` visits
      that batch oldest-of-batch first — acceptable because the batch
      already IS the player's newest games and ``get_recent_games``
      surfaces the same ``created_at DESC`` rows the pass scores.
    * Per-row commit — a crash mid-pass loses at most one game's work.
    * Cancellation-aware — when ``job`` is provided, an external status
      flip (``unlink_account`` marks the job failed) stops the loop
      between games, mirroring the import stream's per-game cancellation
      granularity: up to one in-flight game (typically ~16 s of engine
      time) completes after the flip before the pass notices.
    * Fail-soft per row — a malformed PGN (near-unreachable: the import
      stream rejects PGNs python-chess can't replay, IS_13) is scored
      with the recompute's own degenerate-game fallback (``accuracy=0.5``,
      empty weaknesses) so it leaves the unscored set instead of wedging
      the cap on every future pass.
    * Fail-stop on pool trouble — ``RuntimeError`` (pool saturation /
      unavailability) aborts the pass; remaining rows stay unscored and
      the NEXT import job retries them.  The import job itself still
      succeeds: analysis is enrichment, not import correctness.
    * Never mutates ``Player.rating`` / ``confidence``.

    Returns the number of rows analysed.
    """
    cap = LICHESS_ANALYSIS_MAX_GAMES if max_games_analyzed is None else max_games_analyzed
    if cap <= 0:
        return 0

    rows = (
        db.query(GameEvent)
        .filter(
            GameEvent.player_id == player.id,
            GameEvent.source == _PLATFORM_LICHESS,
            _unscored_filter(),
        )
        .order_by(GameEvent.created_at.desc())
        .limit(cap)
        .all()
    )

    analyzed = 0
    for row in rows:
        if job is not None:
            db.refresh(job, ["status"])
            if job.status not in JOB_STATUS_ACTIVE:
                logger.info(
                    "Lichess analysis pass stopping: job %s flipped to %s",
                    job.id,
                    job.status,
                )
                break
        try:
            analysis = compute_accuracy_from_pgn(
                row.pgn,
                engine_pool,
                result=row.result,
                movetime_ms=LICHESS_ANALYSIS_MOVETIME_MS,
            )
        except RuntimeError as exc:
            db.rollback()
            # "Up to": fallback-scored rows (the ValueError branch) leave
            # the unscored set without incrementing ``analyzed``, so the
            # arithmetic is an upper bound, not an exact count.
            logger.warning(
                "Lichess analysis pass aborted (engine pool): %s — up to %d row(s) deferred",
                exc,
                len(rows) - analyzed,
            )
            break
        except ValueError as exc:
            db.rollback()
            logger.warning("Lichess analysis: unparseable PGN on row %s: %s", row.id, exc)
            row.accuracy = 0.5
            row.weaknesses_json = "{}"
            db.commit()
            continue

        row.accuracy = float(analysis.accuracy)
        row.weaknesses_json = json.dumps(analysis.weaknesses)
        db.commit()
        analyzed += 1
        if job is not None:
            job.analyzed = analyzed
            db.commit()

    return analyzed
