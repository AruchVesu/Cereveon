"""Server-side accuracy + weakness recompute from a finished game's PGN.

Closes the trust gap previously documented in
``docs/SECA.md`` under "Trust property of the reward signal":
``/game/finish`` accepted client-supplied ``accuracy`` and
``weaknesses`` at face value, feeding the SECA loop's reward signal
from a value the Android client controls.  A modded client could
inflate ``accuracy`` toward 1.0 and shift every subsequent bandit
context vector toward the high-accuracy regime.

This module re-analyses the submitted PGN with the engine pool,
classifies each player move via centipawn-loss thresholds aligned with
``llm.seca.analysis.mistake_classifier``, and returns the canonical
``accuracy`` + ``weakness`` dict driven by engine truth.

Performance notes
-----------------
The default ``movetime_ms=200`` gives the engine enough depth
(~12-14 plies on opening positions) to catch the slow-burn losses
that depth-7-10 shallow eval was missing (e.g., a player walking
their king out gradually — each individual king step might look
< 100 cp at shallow depth, while the cumulative position is already
losing).  A 40-move game with a cold cache finishes in ~8 s;
``FenMoveCache`` populated during live play makes most positions
cache hits.  Each move requires exactly one engine call
(the position-after-the-move serves as the position-before for the
next iteration).

The 200 ms default was raised from 50 ms on 2026-05-20 after the
mistake-replay detector consistently surfaced downstream
catastrophes (move N where eval collapsed by thousands of cp)
instead of the originating slow-burn mistake (moves earlier where
the player gradually lost position).  See ``llm.seca.mistakes.detector``
for the companion dual-signal picker (transition + single-move-delta)
that closes the gap on the detector side.

The function raises on engine-pool unavailability or malformed PGNs;
the caller (``llm.seca.events.router.finish_game``) catches and
falls back to client-supplied values with a warning log line.  The
fallback preserves the existing /game/finish flow but loses
anti-cheat coverage — operators surface this via the
``ACC_FALLBACK`` log signal.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import chess
import chess.pgn

from llm.seca.engines.stockfish.pool import StockfishEnginePool

logger = logging.getLogger(__name__)


# Per-move classification thresholds (centipawn loss from the mover's
# perspective).  Aligned with ``llm.seca.analysis.mistake_classifier``
# (50 / 150 / 300) so the live-play classifier and the post-game
# recompute speak the same vocabulary.
_INACCURACY_THRESHOLD_CP = 50
_MISTAKE_THRESHOLD_CP = 150
_BLUNDER_THRESHOLD_CP = 300

# Mate values are collapsed to ±10000 cp so the centipawn-delta math
# below works without special-casing mate-vs-mate transitions.  10000
# is large enough that any non-mate eval is dominated by it.
_MATE_VALUE_CP = 10000

# Safety cap on moves analysed — paranoia against pathological PGNs
# (extremely long games or runaway PGN constructions).  Real games
# rarely exceed 200 plies; the cap lets us bound /game/finish latency.
_MAX_PLIES_ANALYSED = 200

# Queue timeout for the per-ply slot acquire on the engine pool.  The
# pool's default ``queue_timeout_ms`` (50 ms in production) is right
# for the snappy /live/move path but too aggressive when one
# /game/finish request needs ~40 sequential acquires: even mild
# concurrency from /live/move or /engine/eval can push an acquire
# past 50 ms and demote the whole recompute to the fallback path.
# 1 000 ms gives enough slack to ride out routine contention while
# still bounding tail latency.  Pinned by
# test_pgn_accuracy.test_evaluate_passes_higher_queue_timeout.
_RECOMPUTE_QUEUE_TIMEOUT_MS = 1_000


@dataclass(frozen=True)
class AccuracyAnalysis:
    """Canonical analysis of a completed game.

    All values are derived from engine evaluation of the PGN's moves;
    none are propagated from client-supplied request fields.
    """

    accuracy: float
    """Player-side accuracy in [0, 1].  Higher is better.  Derived from
    Average Centipawn Loss (ACPL) via ``1 / (1 + acpl / 100)`` — a
    diminishing-returns mapping that gives accuracy ~ 1.0 at ACPL=0,
    ~ 0.5 at ACPL=100, ~ 0.25 at ACPL=300."""

    weaknesses: dict[str, float]
    """Player-side weakness vector, phase-keyed.

    Keys are a subset of ``{"opening", "middlegame", "endgame"}``;
    values are (mistake + blunder rate) for the player's moves in
    that phase, normalised over total player moves.  Matches the
    schema ``llm.seca.analytics.mistake_stats.aggregate_from_weakness_dicts``
    expects (see ``llm.seca.analysis.weakness_vector.WeaknessVectorBuilder``
    for the historical producer), so the post-game dominant-category
    pipeline can read directly from ``event.weaknesses_json``.

    Pre-PR-#171 this returned severity-keyed counts
    (``{"blunders", "mistakes", "inaccuracies"}``) and the analytics
    aggregator silently dropped every record — see the 2026-05-16
    multi-game probe finding.  Severity counts are still available as
    ``blunder_count`` / ``mistake_count`` / ``inaccuracy_count`` for
    the ACC_DIVERGENCE log and the bandit context."""

    blunder_count: int
    mistake_count: int
    inaccuracy_count: int
    moves_analyzed: int
    """Number of *player* moves analysed (not total plies)."""

    player_color: chess.Color
    """Which side the analysis was attributed to.  Inferred from the
    PGN's Result tag combined with the player's reported outcome —
    see ``_infer_player_color``."""

    losses_cp: tuple[int, ...]
    """Centipawn loss per player move, in PGN order.  ``losses_cp[i]``
    is the loss for the player's ``(i + 1)``-th half-move; opponent
    moves are not included.  Exposed so downstream consumers (e.g. the
    mistake-replay detector in ``llm.seca.mistakes.detector``) can
    pick a mistake index without re-running the engine.  Stored
    as a tuple so the frozen dataclass remains hashable / immutable;
    callers that need a list can ``list(...)`` it."""

    player_pov_eval_before_cp: tuple[int, ...]
    """Engine eval BEFORE each player move, projected to the player's
    POV (positive = player winning, negative = player losing).  Same
    length as ``losses_cp``; ``[i]`` is the eval the player faced
    going into their ``(i + 1)``-th half-move.  Added 2026-05-20 to
    feed the cumulative-eval transition signal in
    ``llm.seca.mistakes.detector.find_first_mistake`` — the signal
    that catches slow-burn mistakes the single-move-delta picker
    misses (player gradually walks their king out: each step
    < 150 cp loss individually, but eval crosses from "drawn" to
    "lost" on a specific move and that's the lesson)."""

    player_pov_eval_after_cp: tuple[int, ...]
    """Engine eval AFTER each player move, projected to the player's
    POV (sign convention as above).  Same length as ``losses_cp``;
    ``[i]`` is the eval the player produced by their ``(i + 1)``-th
    half-move, BEFORE the opponent's response.  Paired with
    ``player_pov_eval_before_cp[i]`` to compute the transition
    condition ``eval_before > -LOSING_THRESHOLD_CP`` AND
    ``eval_after <= -LOSING_THRESHOLD_CP``."""

    source: str
    """``"engine"`` when the analysis was driven by engine evaluation;
    ``"fallback"`` when ``moves_analyzed == 0`` (empty PGN or all moves
    rejected).  Distinguishes "we analysed and the answer is X" from
    "we couldn't analyse, here's a neutral default."""


def compute_accuracy_from_pgn(
    pgn_text: str,
    engine_pool: StockfishEnginePool,
    *,
    result: str,
    movetime_ms: int = 200,
    max_plies: int = _MAX_PLIES_ANALYSED,
) -> AccuracyAnalysis:
    """Re-analyse ``pgn_text`` and return canonical accuracy + weakness.

    Parameters
    ----------
    pgn_text:
        Full PGN of the completed game.
    engine_pool:
        Live ``StockfishEnginePool`` used for per-move evaluation.
    result:
        The player's reported outcome ("win" / "loss" / "draw").
        Combined with the PGN's Result tag to infer which side was
        the player — see ``_infer_player_color``.
    movetime_ms:
        Per-move analysis budget.  Defaults to 200 ms (raised from
        50 ms on 2026-05-20 — see module docstring "Performance
        notes" for the slow-burn-king-walk regression that prompted
        the bump).  200 ms reaches ~depth 12-14 on opening positions
        and ~depth 8-10 in complex middlegames, deep enough that the
        cumulative-eval transition signal in
        ``llm.seca.mistakes.detector`` can reliably distinguish "you
        crossed from drawn to lost on this move" from sub-threshold
        positional noise.
    max_plies:
        Safety cap on plies analysed.  Defaults to 200.

    Raises
    ------
    ValueError
        On malformed PGN or invalid moves.
    RuntimeError
        On engine-pool unavailability or queue saturation.
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("PGN could not be parsed")

    player_color = _infer_player_color(game, result)
    board = game.board()

    losses_cp: list[int] = []
    # Per-player-move engine eval, projected to the player's POV
    # (positive = player winning, negative = player losing).  Feeds the
    # cumulative-eval transition signal in
    # ``llm.seca.mistakes.detector.find_first_mistake`` — see the
    # AccuracyAnalysis field docstrings for the data contract.
    player_pov_eval_before_cp: list[int] = []
    player_pov_eval_after_cp: list[int] = []
    # Per-phase loss tracking.  Phase classification matches
    # ``llm.seca.analysis.analyzer.GameWeaknessAnalyzer._phase`` so the
    # downstream ``aggregate_from_weakness_dicts`` aggregator (which
    # keys off "opening" / "middlegame" / "endgame") reads phase rates
    # with the same vocabulary the historical producer used.  Each
    # entry is a loss-cp integer for one player move played in that
    # phase.
    phase_losses_cp: dict[str, list[int]] = {
        "opening": [],
        "middlegame": [],
        "endgame": [],
    }
    plies_seen = 0
    prev_eval_cp: int | None = None

    for node in game.mainline():
        if plies_seen >= max_plies:
            break

        side_to_move = board.turn
        is_player_move = side_to_move == player_color
        # Phase is determined by the PRE-MOVE position the player faced,
        # matching WeaknessVectorBuilder.record(phase=_phase(board)) in
        # the historical analyzer (called before board.push).
        pre_move_phase = _phase(board) if is_player_move else None

        if prev_eval_cp is None:
            # First iteration — establish the eval-before for the
            # starting position.  Subsequent iterations reuse the
            # eval-after as the next eval-before.
            prev_eval_cp = _evaluate_cp(engine_pool, board.fen(), movetime_ms)

        try:
            board.push(node.move)
        except (ValueError, AssertionError) as exc:
            raise ValueError(
                f"PGN move {plies_seen + 1} could not be applied: {exc}"
            ) from exc

        after_eval_cp = _evaluate_cp(engine_pool, board.fen(), movetime_ms)

        if is_player_move:
            # Loss from the player's POV — positive when the player's
            # move worsened the eval for their side.  Engine evals are
            # always from White's perspective in centipawns, so:
            #   White moves: loss = prev - after  (eval going down hurts White)
            #   Black moves: loss = after - prev  (eval going up hurts Black)
            if player_color == chess.WHITE:
                loss = max(0, prev_eval_cp - after_eval_cp)
                eval_before_player_pov = prev_eval_cp
                eval_after_player_pov = after_eval_cp
            else:
                loss = max(0, after_eval_cp - prev_eval_cp)
                eval_before_player_pov = -prev_eval_cp
                eval_after_player_pov = -after_eval_cp
            losses_cp.append(loss)
            player_pov_eval_before_cp.append(int(eval_before_player_pov))
            player_pov_eval_after_cp.append(int(eval_after_player_pov))
            if pre_move_phase is not None:
                phase_losses_cp[pre_move_phase].append(loss)

        prev_eval_cp = after_eval_cp
        plies_seen += 1

    return _summarise(
        losses_cp,
        phase_losses_cp,
        player_color,
        player_pov_eval_before_cp,
        player_pov_eval_after_cp,
    )


def _phase(board: chess.Board) -> str:
    """Coarse game-phase classifier — piece-count thresholds match
    ``llm.seca.analysis.analyzer.GameWeaknessAnalyzer._phase``.  Pinned
    by ``test_pgn_accuracy_phase_classifier_matches_historical_analyzer``
    so the two producers can't drift apart.
    """
    pieces = len(board.piece_map())
    if pieces > 24:
        return "opening"
    if pieces > 12:
        return "middlegame"
    return "endgame"


def _evaluate_cp(
    pool: StockfishEnginePool,
    fen: str,
    movetime_ms: int,
) -> int:
    """Evaluate a position and return a centipawn score from White's POV.

    Passes ``queue_timeout_ms=_RECOMPUTE_QUEUE_TIMEOUT_MS`` (1 000 ms,
    not the pool's 50 ms default) because the recompute is a ~40-acquire
    batch that mustn't collapse to the client-value fallback under
    routine concurrent /live/move pressure.  See module docstring.

    Mate-in-N is collapsed to ±``_MATE_VALUE_CP`` (positive for White
    mating, negative for Black mating) so downstream centipawn-delta
    arithmetic works without special-casing the type field.
    """
    result = pool.evaluate_position(
        fen=fen,
        movetime_ms=movetime_ms,
        queue_timeout_ms=_RECOMPUTE_QUEUE_TIMEOUT_MS,
    )
    evaluation = result.get("evaluation", {})
    eval_type = evaluation.get("type")
    value = int(evaluation.get("value", 0))
    if eval_type == "mate":
        return _MATE_VALUE_CP if value > 0 else -_MATE_VALUE_CP
    return value


def _infer_player_color(game: chess.pgn.Game, result: str) -> chess.Color:
    """Match the PGN's Result tag against the player's reported outcome.

    The Result tag (``"1-0"``, ``"0-1"``, ``"1/2-1/2"``, or ``"*"``)
    plus the player's report (``"win"`` / ``"loss"`` / ``"draw"``)
    pins which side the player was on.  Faking both fields
    simultaneously while keeping the PGN moves realistic is much
    harder than the bypass this module closes (just send
    ``accuracy=1.0``).

    Draws + unknown defaults to White.  Per-side ACPL in a drawn game
    is usually comparable for both sides, so the choice rarely
    matters downstream.
    """
    pgn_result = (game.headers.get("Result") or "").strip()
    result_norm = result.lower().strip()

    if result_norm == "win":
        return chess.WHITE if pgn_result == "1-0" else chess.BLACK
    if result_norm == "loss":
        return chess.BLACK if pgn_result == "1-0" else chess.WHITE
    return chess.WHITE


def _summarise(
    losses_cp: list[int],
    phase_losses_cp: dict[str, list[int]],
    player_color: chess.Color,
    player_pov_eval_before_cp: list[int],
    player_pov_eval_after_cp: list[int],
) -> AccuracyAnalysis:
    """Reduce per-move CP losses to accuracy + phase-keyed weakness rates.

    ``weaknesses`` keys are the subset of ``{"opening", "middlegame",
    "endgame"}`` that produced at least one player move classified as
    mistake or blunder (``loss >= _MISTAKE_THRESHOLD_CP``).  Values are
    that phase's mistake+blunder count normalised over the TOTAL count
    of player moves analysed.  Matches the historical
    ``WeaknessVectorBuilder.build()`` shape so the downstream analytics
    aggregator (``aggregate_from_weakness_dicts``) reads category
    scores out of the box.
    """
    if not losses_cp:
        return AccuracyAnalysis(
            accuracy=0.5,
            weaknesses={},
            blunder_count=0,
            mistake_count=0,
            inaccuracy_count=0,
            moves_analyzed=0,
            player_color=player_color,
            losses_cp=(),
            player_pov_eval_before_cp=(),
            player_pov_eval_after_cp=(),
            source="fallback",
        )

    blunders = sum(1 for loss in losses_cp if loss >= _BLUNDER_THRESHOLD_CP)
    mistakes = sum(
        1
        for loss in losses_cp
        if _MISTAKE_THRESHOLD_CP <= loss < _BLUNDER_THRESHOLD_CP
    )
    inaccuracies = sum(
        1
        for loss in losses_cp
        if _INACCURACY_THRESHOLD_CP <= loss < _MISTAKE_THRESHOLD_CP
    )

    acpl = sum(losses_cp) / len(losses_cp)
    # Diminishing-returns ACPL → accuracy mapping.  Calibration:
    #   ACPL=0   -> 1.00
    #   ACPL=20  -> 0.83
    #   ACPL=50  -> 0.67
    #   ACPL=100 -> 0.50
    #   ACPL=200 -> 0.33
    #   ACPL=300 -> 0.25
    accuracy = max(0.0, min(1.0, 1.0 / (1.0 + acpl / 100.0)))

    n = len(losses_cp)

    # Phase-keyed weaknesses: rate of (mistake + blunder) for each phase,
    # normalised over total player moves.  Matches
    # WeaknessVectorBuilder.build() semantics: count moves where the
    # classified delta is "mistake" (>= _MISTAKE_THRESHOLD_CP) or
    # "blunder" (>= _BLUNDER_THRESHOLD_CP).  Inaccuracies are excluded
    # so the rate matches the historical producer that fed
    # _PHASE_TO_CATEGORIES in mistake_stats.py.  Only phases that
    # actually produced significant errors are emitted — zero-rate
    # phases are dropped to keep event.weaknesses_json compact.
    weaknesses: dict[str, float] = {}
    for phase, phase_losses in phase_losses_cp.items():
        significant = sum(1 for loss in phase_losses if loss >= _MISTAKE_THRESHOLD_CP)
        if significant > 0:
            weaknesses[phase] = significant / n

    return AccuracyAnalysis(
        accuracy=accuracy,
        weaknesses=weaknesses,
        blunder_count=blunders,
        mistake_count=mistakes,
        inaccuracy_count=inaccuracies,
        moves_analyzed=n,
        player_color=player_color,
        losses_cp=tuple(losses_cp),
        player_pov_eval_before_cp=tuple(player_pov_eval_before_cp),
        player_pov_eval_after_cp=tuple(player_pov_eval_after_cp),
        source="engine",
    )
