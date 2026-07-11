"""Deterministic critical-moment selection for the post-game review.

Pure math over the per-position eval series that
``compute_accuracy_from_pgn`` already produces — no engine calls, no
LLM, no I/O.  The review job replays the PGN once (python-chess only)
to pair each ply with its FEN/SAN, classifies the player's moves, and
selects the top-3 *critical moments* per the feature spec's rules:

* skip the first ``OPENING_BOOK_PLIES`` plies (opening theory),
* at most 2 negative moments among the 3, at least one non-negative
  where possible,
* selected moments at least ``MIN_PLY_GAP`` plies apart,
* no qualifying moment at all → the 3 highest-loss moves with
  ``review_mode="strategic"`` (softer coach language downstream).

Moment types (adapted from the spec for a no-multipv engine budget —
every signal below is derivable from the single eval series):

* ``blunder``          — loss ≥ 300 cp (aligned with pgn_accuracy).
* ``missed_win``       — the player was clearly winning (≥ +200 cp,
  the same magnitude ``llm.seca.mistakes.detector.LOSING_THRESHOLD_CP``
  uses for "clearly lost") and gave ≥ 150 cp back.
* ``mistake``          — loss in [150, 300).
* ``punished_mistake`` — the opponent's previous move swung ≥ 150 cp
  toward the player and the player kept it (loss ≤ 50 cp).  This is the
  spec's "great find" adapted to eval-series-only detection: confirming
  "played the engine's unique best move" needs multipv data the pool
  does not expose, whereas "found and held the punishment" is fully
  determined by the series and is the positive moment the diversity
  rule wants.

Wire safety: everything this module emits for persistence/serving is
**banded** (the five player-relative Atrium steps) — raw centipawns
stay inside the process so the client cannot render numeric evals.

Time-pressure moments (spec §3.2) are deliberately not a type in v1:
Lichess ``[%clk]`` data is present but move-time attribution needs the
increment bookkeeping to be honest.  The remaining clock IS captured
per moment for card context ("you had 12 seconds here").
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import chess
import chess.pgn

# Deliberate private imports — these constants/classifiers are the
# single source of truth for move-severity vocabulary and game phase;
# re-declaring them here would be drift bait (same pattern as the
# lichess router importing ``auth.router._is_sqlite``).  Alignment is
# additionally pinned by tests/test_review_moments.py.
from llm.seca.analysis.pgn_accuracy import (  # pylint: disable=protected-access
    _BLUNDER_THRESHOLD_CP,
    _MISTAKE_THRESHOLD_CP,
    _phase,
)

logger = logging.getLogger(__name__)

#: Plies exempt from moment selection — opening theory (spec §3.3).
OPENING_BOOK_PLIES = 8

#: Minimum distance between two selected moments (spec's "3 plies
#: apart" distance rule).
MIN_PLY_GAP = 3

#: Player-POV eval at/above which the player counts as "clearly
#: winning" for missed_win detection.  Mirrors the magnitude of
#: ``llm.seca.mistakes.detector.LOSING_THRESHOLD_CP`` (200 cp = up two
#: pawns or equivalent) on the winning side.
WINNING_THRESHOLD_CP = 200

#: Max loss that still counts as "kept the punishment" for
#: punished_mistake — the inaccuracy floor from pgn_accuracy.
HOLD_LOSS_MAX_CP = 50

#: Opponent-error swing that arms punished_mistake detection.
OPPONENT_ERROR_SWING_CP = 150

#: A game shorter than this many plies is not reviewable (spec §12:
#: "Game < 15 moves → too short to review").
MIN_REVIEW_PLIES = 30

MOMENT_BLUNDER = "blunder"
MOMENT_MISSED_WIN = "missed_win"
MOMENT_MISTAKE = "mistake"
MOMENT_PUNISHED_MISTAKE = "punished_mistake"
MOMENT_STRATEGIC = "strategic"

#: Types that read as "something went wrong" — capped at 2 of 3 by the
#: diversity rule.  ``strategic`` counts as neutral (it only appears in
#: strategic mode, where every card uses softer language anyway).
NEGATIVE_MOMENT_TYPES = frozenset({MOMENT_BLUNDER, MOMENT_MISSED_WIN, MOMENT_MISTAKE})

REVIEW_MODE_STANDARD = "standard"
REVIEW_MODE_STRATEGIC = "strategic"

#: The five player-relative display steps — the EXACT vocabulary of the
#: Android ``EvalBandView`` (the only allowed eval visual).  The wire
#: payload carries these strings; the client maps them 1:1 onto its
#: existing enum and cannot invent numeric detail it never received.
BAND_LOSING = "losing"
BAND_WORSE = "worse"
BAND_EQUAL = "equal"
BAND_BETTER = "better"
BAND_WINNING = "winning"

#: ESV band cutoffs, in centipawns of absolute eval.  MUST stay aligned
#: with ``llm.rag.engine_signal.extract_engine_signal`` (equal ≤ 20 <
#: small ≤ 60 < clear ≤ 120 < decisive); the alignment is pinned by
#: ``test_review_moments.py::TestBandAlignment`` so the two producers
#: cannot drift apart silently.  small/clear collapse onto one display
#: step because the Atrium band view renders five steps, not seven.
_EQUAL_MAX_CP = 20
_DECISIVE_MIN_CP = 121


def band_for_player_cp(player_pov_cp: int) -> str:
    """Map a player-POV centipawn eval onto the five Atrium band steps."""
    if player_pov_cp <= -_DECISIVE_MIN_CP:
        return BAND_LOSING
    if player_pov_cp < -_EQUAL_MAX_CP:
        return BAND_WORSE
    if player_pov_cp <= _EQUAL_MAX_CP:
        return BAND_EQUAL
    if player_pov_cp < _DECISIVE_MIN_CP:
        return BAND_BETTER
    return BAND_WINNING


@dataclass(frozen=True)
class MoveRecord:
    """One player move paired with its engine-truth context."""

    ply: int
    """1-based ply index in the mainline; ``eval series[ply]`` is the
    position after this move, ``series[ply - 1]`` before it."""

    move_number: int
    """Move number as shown in the PGN / replay header (1-based)."""

    san: str
    fen_before: str
    fen_after: str
    phase: str
    """opening | middlegame | endgame — classified on the PRE-move
    position by the same piece-count classifier pgn_accuracy uses."""

    before_cp: int
    """Eval before the move, player POV (positive = player ahead)."""

    after_cp: int
    """Eval after the move, player POV."""

    loss_cp: int
    """max(0, before - after) — centipawns the move gave away."""

    opp_prior_swing_cp: int
    """Player-POV swing produced by the opponent's IMMEDIATELY
    preceding ply (0 for the player's first recorded move).  Positive =
    the opponent's move helped the player, i.e. was an error."""

    clock_remaining_s: int | None
    """Seconds left on the player's clock after this move, from the
    PGN's ``[%clk]`` annotations; None when the PGN carries no clocks."""


@dataclass(frozen=True)
class CriticalMoment:
    """One selected review moment — a classified :class:`MoveRecord`."""

    record: MoveRecord
    moment_type: str
    score: float

    def to_payload(self) -> dict:
        """Wire-safe dict for persistence and serving.

        Deliberately carries NO raw centipawns — band strings only.
        The stored JSON is served to the client verbatim, so keeping
        this shape numeric-free enforces the no-numeric-eval invariant
        at the wire instead of trusting the client to round.
        """
        rec = self.record
        return {
            "ply": rec.ply,
            "move_number": rec.move_number,
            "san": rec.san,
            "moment_type": self.moment_type,
            "phase": rec.phase,
            "band_before": band_for_player_cp(rec.before_cp),
            "band_after": band_for_player_cp(rec.after_cp),
            "fen_before": rec.fen_before,
            "fen_after": rec.fen_after,
            "clock_remaining_s": rec.clock_remaining_s,
        }


def build_player_move_records(
    pgn_text: str,
    white_pov_series_cp: tuple[int, ...] | list[int],
    *,
    player_is_white: bool,
) -> list[MoveRecord]:
    """Replay ``pgn_text`` and pair the player's moves with the series.

    ``white_pov_series_cp`` is
    ``AccuracyAnalysis.white_pov_eval_per_position_cp``: index 0 = start
    position, index i = after ply i.  Plies beyond the series (the
    accuracy recompute's ``max_plies`` cap) are not recorded — the
    review simply does not consider them, matching the accuracy math.

    Returns an empty list on unparseable PGN (callers treat that as
    "nothing to review"; the import stream already rejected PGNs
    python-chess cannot replay, so this is a legacy-row guard, not an
    expected path).
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        logger.warning("review moments: PGN could not be parsed; no records built")
        return []

    player_color = chess.WHITE if player_is_white else chess.BLACK
    board = game.board()
    records: list[MoveRecord] = []
    prev_player_pov_swing = 0
    ply = 0

    for node in game.mainline():
        ply += 1
        if ply >= len(white_pov_series_cp):
            # series[ply] must exist (position after this ply); the
            # accuracy walk stopped here, so the review stops too.
            break

        mover = board.turn
        fen_before = board.fen()
        move_number = board.fullmove_number
        pre_phase = _phase(board)
        try:
            san = board.san(node.move)
            board.push(node.move)
        except (ValueError, AssertionError):
            logger.warning("review moments: illegal move at ply %d; stopping walk", ply)
            break

        white_before = white_pov_series_cp[ply - 1]
        white_after = white_pov_series_cp[ply]
        sign = 1 if player_is_white else -1
        player_before = sign * white_before
        player_after = sign * white_after

        if mover == player_color:
            clock = node.clock()
            records.append(
                MoveRecord(
                    ply=ply,
                    move_number=move_number,
                    san=san,
                    fen_before=fen_before,
                    fen_after=board.fen(),
                    phase=pre_phase,
                    before_cp=player_before,
                    after_cp=player_after,
                    loss_cp=max(0, player_before - player_after),
                    opp_prior_swing_cp=prev_player_pov_swing,
                    clock_remaining_s=None if clock is None else int(clock),
                )
            )
        # Swing produced by THIS ply, from the player's POV — consumed
        # by the player's next move as ``opp_prior_swing_cp`` when this
        # ply was the opponent's.
        prev_player_pov_swing = player_after - player_before

    return records


def _classify(record: MoveRecord) -> tuple[str, float] | None:
    """Return (moment_type, score) for a record, or None if unremarkable.

    Scores are ordinal ranks, not calibrated probabilities: base weight
    per type (spec §3.2 High/Medium) plus a small magnitude bonus so
    bigger swings win ties within a type.  Precedence: a ≥300 cp loss
    from a winning position is typed ``missed_win`` (the lesson is "you
    were winning", not "you blundered") — checked first.
    """
    magnitude = min(record.loss_cp, 1000) / 1000.0  # 0..1 tiebreak bonus
    if record.before_cp >= WINNING_THRESHOLD_CP and record.loss_cp >= _MISTAKE_THRESHOLD_CP:
        return (MOMENT_MISSED_WIN, 100.0 + magnitude)
    if record.loss_cp >= _BLUNDER_THRESHOLD_CP:
        return (MOMENT_BLUNDER, 90.0 + magnitude)
    if record.opp_prior_swing_cp >= OPPONENT_ERROR_SWING_CP and record.loss_cp <= HOLD_LOSS_MAX_CP:
        swing_bonus = min(record.opp_prior_swing_cp, 1000) / 1000.0
        return (MOMENT_PUNISHED_MISTAKE, 70.0 + swing_bonus)
    if record.loss_cp >= _MISTAKE_THRESHOLD_CP:
        return (MOMENT_MISTAKE, 60.0 + magnitude)
    return None


def select_critical_moments(
    records: list[MoveRecord],
) -> tuple[list[CriticalMoment], str]:
    """Select up to 3 critical moments per the spec's rules.

    Returns ``(moments_sorted_by_ply, review_mode)``.  ``strategic``
    mode fires when no move past the opening cleared any type threshold
    — the fallback then surfaces the 3 highest-loss moves so the coach
    still has something concrete to discuss, with softer language
    selected downstream via the mode flag.
    """
    eligible = [r for r in records if r.ply > OPENING_BOOK_PLIES]

    candidates: list[CriticalMoment] = []
    for rec in eligible:
        classified = _classify(rec)
        if classified is not None:
            candidates.append(CriticalMoment(rec, classified[0], classified[1]))

    mode = REVIEW_MODE_STANDARD
    if not candidates:
        mode = REVIEW_MODE_STRATEGIC
        candidates = [
            CriticalMoment(rec, MOMENT_STRATEGIC, float(rec.loss_cp))
            for rec in eligible
            if rec.loss_cp > 0
        ]

    ranked = sorted(candidates, key=lambda c: (-c.score, c.record.ply))

    positive_available = any(c.moment_type not in NEGATIVE_MOMENT_TYPES for c in ranked)

    selected: list[CriticalMoment] = []
    # Pass 1 — respect the diversity cap (≤ 2 negative) whenever a
    # positive candidate exists to take the third slot.
    for cand in ranked:
        if len(selected) == 3:
            break
        if _too_close(cand, selected):
            continue
        negatives = sum(1 for s in selected if s.moment_type in NEGATIVE_MOMENT_TYPES)
        if cand.moment_type in NEGATIVE_MOMENT_TYPES and negatives >= 2 and positive_available:
            continue
        selected.append(cand)

    # Pass 2 — the diversity cap may have left slots empty (e.g. the
    # only positive candidates sat within MIN_PLY_GAP of a pick).  Fill
    # remaining slots with the best unpicked candidates of any type —
    # three negative moments beat two cards.
    if len(selected) < 3:
        for cand in ranked:
            if len(selected) == 3:
                break
            if cand in selected or _too_close(cand, selected):
                continue
            selected.append(cand)

    selected.sort(key=lambda c: c.record.ply)
    return selected, mode


def _too_close(cand: CriticalMoment, selected: list[CriticalMoment]) -> bool:
    return any(abs(cand.record.ply - s.record.ply) < MIN_PLY_GAP for s in selected)
