"""Deterministic move-quality classification from before/after engine evals.

Mode-1 (``/live/move``) used to leave ``last_move_quality`` as ``"unknown"``:
the handler ran a single Stockfish eval on the POST-move position, so it had no
before-eval to compare against and the coach attributed the *current*
evaluation to the move as blame ("you played f5 -> the opponent is better")
even for good, thematic moves (Mode-1 complex-position probe, 2026-06-19).

Given the engine eval of the position BEFORE the player's move and AFTER it
(both as ``evaluate_position`` dicts), these helpers compute the
player-perspective centipawn loss and classify it into the closed move-quality
vocabulary that ``extract_engine_signal`` and the Mode-1 pipeline already
consume (``errors.last_move_quality``).

The grade is coarse by design — a 200 ms before/after eval carries real noise,
so the bands are forgiving and only the clear cases (a true blunder, a clean
best move) are graded confidently.  It is NOT a precise engine annotation.
"""

from __future__ import annotations

# Centipawn-loss bands (player perspective): how much the evaluation dropped by
# playing this move instead of holding the prior (best-play) evaluation.  Kept
# forgiving so 200 ms eval noise doesn't grade a normal move as a mistake.
_BEST_MAX = 20
_GOOD_MAX = 60
_INACCURACY_MAX = 120
_MISTAKE_MAX = 250

# Mate scores map to a large centipawn magnitude so the before/after maths works
# across cp<->mate transitions (throwing away a forced mate, or walking into
# one, must register as a swing).  Mirrors the pool's ``score(mate_score=10000)``
# convention; a closer mate is slightly larger in magnitude.
_MATE_CP = 10_000


def eval_to_player_cp(sf_eval: dict, player_is_white: bool) -> int:
    """Convert an ``evaluate_position`` eval dict to player-perspective cp.

    ``value`` is White-relative (positive = White better) for ``"cp"``, or a
    signed mate-in-N (positive = White mates) for ``"mate"``.  Mate is collapsed
    to +/-``_MATE_CP`` (closer mates marginally larger) so it compares against a
    centipawn score.  ``mate`` with value 0 is a terminal checkmate whose winner
    isn't determinable here without the side to move; the ``/live/move`` flow
    never evaluates a terminal position (the client skips the game-ending move),
    so it returns 0 (neutral) rather than guess.
    """
    if not isinstance(sf_eval, dict):
        return 0
    etype = sf_eval.get("type", "cp")
    try:
        value = int(sf_eval.get("value", 0))
    except (TypeError, ValueError):
        value = 0
    if etype == "mate":
        if value > 0:
            white_cp = _MATE_CP - value
        elif value < 0:
            white_cp = -_MATE_CP - value
        else:
            white_cp = 0
    else:
        white_cp = value
    return white_cp if player_is_white else -white_cp


def classify_move_quality(player_cp_before: int, player_cp_after: int) -> str:
    """Classify the move by player-perspective centipawn loss.

    ``loss = before - after``, clamped at 0 — engine noise or a move that
    "improves" on the prior best-play evaluation grades as ``"best"``.  Returns
    one of ``best`` / ``good`` / ``inaccuracy`` / ``mistake`` / ``blunder`` (all
    in ``extract_engine_signal``'s ``_KNOWN_MOVE_QUALITIES``).
    """
    loss = max(0, player_cp_before - player_cp_after)
    if loss <= _BEST_MAX:
        return "best"
    if loss <= _GOOD_MAX:
        return "good"
    if loss <= _INACCURACY_MAX:
        return "inaccuracy"
    if loss <= _MISTAKE_MAX:
        return "mistake"
    return "blunder"
