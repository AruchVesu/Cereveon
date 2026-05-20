"""Pick the first mistake the player made in a finished game.

Given the ``AccuracyAnalysis`` already produced at /game/finish time
(which carries per-player-move centipawn-loss numbers in ``losses_cp``
plus the running player-POV engine eval), walk the PGN in move order
and return the FIRST player move that trips either of two complementary
signals.  Return a ``FirstMistake`` describing:

* the FEN the player was looking at when they erred,
* the move they actually played (UCI),
* the move number they'll see in the replay sheet header,
* the eval loss (centipawns).

Dual-signal picker
------------------
A single-move-delta picker ("first move with loss >= 150 cp") is
robust for sudden blunders but blind to slow-burn mistakes: a player
walking their king out across moves 1-3, where each step is < 150 cp
loss individually but the cumulative position is already lost.  The
2026-05-20 king-walk regression (move 4 surfaced with 9743 cp loss as
Stockfish finally noticed mate, instead of move 1 where the king walk
started) prompted the addition of a cumulative-eval transition signal:

* **Transition** — the player's POV eval crossed from "OK" (above
  ``-LOSING_THRESHOLD_CP``) to "losing" (at or below
  ``-LOSING_THRESHOLD_CP``) on this move.  This catches the slow-burn
  case directly: surfaces the move where the player went from drawn-ish
  to clearly lost, regardless of single-move-delta magnitude.

* **Single-move delta** — the player's loss on this move clears
  ``MIN_MISTAKE_LOSS_CP`` (150 cp).  This catches sudden blunders that
  happen while the player was already losing (transition can't fire
  because ``eval_before`` was already below the losing floor).

The picker checks BOTH conditions per player move during a single PGN
walk and returns on whichever fires first in move order.  In practice
the transition signal usually fires earlier (it has a lower effective
bar — a 100 cp single-move move that crosses the equality band still
trips it), so the transition catches the originating mistake while
single-move-delta acts as a safety net for the "you were already
losing" case.

Pedagogical rationale for "first" rather than "largest"
-------------------------------------------------------
A losing game often contains a single originating blunder followed by
several larger-centipawn-loss moves that are downstream symptoms — you
hung a piece on move 14, then on move 22 you tried to save the
position and gave up the exchange.  The 22-cp swing is bigger, but the
14-cp swing is the lesson.  Surfacing the first qualifying move
teaches the user to avoid the root error before it cascades.

This is also the reason the detector intentionally does NOT compute
the engine's preferred move here.  That comes later, on the verify
path: the replay sheet shows the position + the user's bad move, lets
them try a new one, and the ``POST /training/verify-replay`` endpoint
compares the attempt against the engine's best move at that point.
Keeping the detector engine-call-free at /game/finish time means
mistake extraction is essentially free piggy-backed on the accuracy
recompute the route already runs.

When no move trips either signal (a clean game), the detector returns
``None`` and the caller omits the ``biggest_mistake`` field from the
response.

Wire-name note
--------------
The /game/finish response field is still ``biggest_mistake`` for
backward compatibility with the Android client decoded shape.  The
field name is a historical artifact of PR #192's original "pick the
worst loss" picker; the wire contract was kept stable when the
selection policy evolved (first → first-above-threshold →
first-via-dual-signal) so old client builds keep decoding.  Internal
Python identifiers (``FirstMistake``, ``find_first_mistake``) reflect
the actual semantics.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import chess
import chess.pgn

logger = logging.getLogger(__name__)


# Floor on what counts as a single-move "mistake" worth surfacing for
# replay.  Aligned with ``llm.seca.analysis.pgn_accuracy._MISTAKE_THRESHOLD_CP``
# (150 cp) so the replay UI only fires on a real mistake (mistake or
# blunder severity), not on a borderline inaccuracy that the user
# could reasonably argue was fine.
MIN_MISTAKE_LOSS_CP: int = 150


# Centipawn floor below which the player is considered "lost" from their
# own POV.  The transition signal fires when ``eval_before`` was
# strictly greater than ``-LOSING_THRESHOLD_CP`` (player was OK or only
# slightly worse) and ``eval_after`` is at or below
# ``-LOSING_THRESHOLD_CP`` (player is now clearly lost).  200 cp is
# "down two pawns or equivalent" — past this point a typical
# coaching-app player has a hard time recovering, so the move that
# crossed this line is the move worth teaching.
LOSING_THRESHOLD_CP: int = 200


@dataclass(frozen=True)
class FirstMistake:
    """One identified mistake-replay target — the player's first move
    that tripped either the single-move-delta signal
    (loss >= ``MIN_MISTAKE_LOSS_CP``) or the cumulative-eval
    transition signal (crossed from OK to lost).
    """

    fen_before: str
    """FEN of the position the player was looking at, BEFORE they made
    the bad move.  The Android replay sheet renders this on a
    ChessBoardView and lets the user try a new move from the same
    starting position."""

    played_uci: str
    """The move the player actually played, in UCI notation (e.g.
    ``e2e4`` or ``e7e8q``).  Used by the replay sheet's header copy —
    "You played Nxe5 — find a stronger move" — and to short-circuit the
    verifier if the user just re-submits the same wrong move."""

    move_number: int
    """1-indexed half-move count for the player's mistake.  ``1`` means
    the player's first move of the game (which for Black would
    actually be ply 2).  This is "the Nth player move" not "the Nth
    ply" — matches what the user sees ("you blundered on move 14")."""

    eval_loss_cp: int
    """Centipawn loss for this single move, from the player's POV.
    On the single-move-delta path this is always >= ``MIN_MISTAKE_LOSS_CP``.
    On the transition path the single-move loss can be lower (e.g. a
    100 cp move that crossed the equality band).  Surfaced on the
    replay sheet so the user knows how big the hole on this specific
    move was."""


def find_first_mistake(
    pgn_text: str,
    losses_cp: tuple[int, ...] | list[int],
    player_pov_eval_before_cp: tuple[int, ...] | list[int],
    player_pov_eval_after_cp: tuple[int, ...] | list[int],
    player_color: chess.Color,
) -> FirstMistake | None:
    """Return the FIRST mistake the player made in ``pgn_text``.

    Walks the PGN once in move order and stops at the first player move
    that trips either of the two complementary signals — see module
    docstring for the design rationale.

    Parameters
    ----------
    pgn_text:
        The same PGN the accuracy recompute consumed.  Re-parsed here
        rather than threaded through the AccuracyAnalysis dataclass so
        the analysis result stays small and copy-friendly.
    losses_cp:
        Per-player-move centipawn losses in PGN order, as produced by
        ``compute_accuracy_from_pgn`` and exposed via
        ``AccuracyAnalysis.losses_cp``.  ``losses_cp[i]`` is the loss
        on the player's ``i+1``-th half-move.
    player_pov_eval_before_cp:
        Engine eval BEFORE each player move, projected to the player's
        POV.  ``AccuracyAnalysis.player_pov_eval_before_cp``.  Same
        length as ``losses_cp``.
    player_pov_eval_after_cp:
        Engine eval AFTER each player move, projected to the player's
        POV.  ``AccuracyAnalysis.player_pov_eval_after_cp``.  Same
        length as ``losses_cp``.
    player_color:
        Which side the analysis was attributed to.  Used to skip
        opponent moves while walking the PGN.

    Returns
    -------
    ``FirstMistake`` describing the first player move that tripped
    either signal, or ``None`` when no move did (a clean game, an
    empty PGN, or losses_cp / PGN drift).

    Errors
    ------
    Malformed PGN or per-move data that doesn't line up with the
    PGN's player-move count both return ``None`` rather than raising
    — /game/finish must not 500 because the detector mis-counted; the
    caller falls back to omitting the field.
    """
    if not losses_cp:
        return None

    losses_list = list(losses_cp)
    eval_before_list = list(player_pov_eval_before_cp)
    eval_after_list = list(player_pov_eval_after_cp)

    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
    except (ValueError, RuntimeError) as exc:
        logger.warning("Mistake detector: PGN parse failed: %s", exc)
        return None
    if game is None:
        logger.warning("Mistake detector: PGN parse returned None")
        return None

    board = game.board()
    player_moves_seen = 0
    for node in game.mainline():
        side_to_move = board.turn
        is_player_move = side_to_move == player_color

        if is_player_move and player_moves_seen < len(losses_list):
            loss = losses_list[player_moves_seen]

            # Single-move-delta signal: a clear sudden mistake on this
            # specific move.  Fires regardless of where the player
            # was on the equality / losing spectrum before / after.
            single_move_trip = loss >= MIN_MISTAKE_LOSS_CP

            # Transition signal: this move took the player from OK to
            # clearly lost.  Requires both eval lists to have a
            # matching entry; if either is missing (drift), the
            # transition can't fire and we rely on the single-move
            # signal alone.
            transition_trip = False
            if player_moves_seen < len(eval_before_list) and player_moves_seen < len(
                eval_after_list
            ):
                eval_before = eval_before_list[player_moves_seen]
                eval_after = eval_after_list[player_moves_seen]
                transition_trip = (
                    eval_before > -LOSING_THRESHOLD_CP and eval_after <= -LOSING_THRESHOLD_CP
                )

            if single_move_trip or transition_trip:
                # ``board`` is the position the player faced; ``node.move``
                # is what they played.  Capture both before mutating
                # ``board`` (so we don't return a post-move FEN).
                return FirstMistake(
                    fen_before=board.fen(),
                    played_uci=node.move.uci(),
                    # 1-indexed for user-facing display.
                    move_number=player_moves_seen + 1,
                    eval_loss_cp=int(loss),
                )

        if is_player_move:
            player_moves_seen += 1

        try:
            board.push(node.move)
        except (ValueError, AssertionError) as exc:
            logger.warning(
                "Mistake detector: failed to apply ply %d: %s",
                player_moves_seen,
                exc,
            )
            return None

    # Walked the entire PGN without tripping either signal.  Three
    # reasons we land here:
    #   1. Clean game — every player loss < MIN_MISTAKE_LOSS_CP and
    #      the player never crossed -LOSING_THRESHOLD_CP.
    #   2. Player was always lost — eval_before was already at or below
    #      -LOSING_THRESHOLD_CP from move 1, so the transition signal
    #      couldn't fire, and individual moves were all < 150 cp loss.
    #   3. losses_cp / PGN drift — losses_cp claimed more player moves
    #      than the PGN contains, and the only above-threshold entries
    #      lived past the PGN's last move.
    # All three collapse to the same caller-visible answer ("no mistake
    # worth replaying"); we log the drift case so an upstream
    # AccuracyAnalysis bug surfaces in operator logs.
    if len(losses_list) > player_moves_seen:
        logger.warning(
            "Mistake detector: losses_cp claimed %d player moves but PGN "
            "yielded only %d; no in-range loss cleared threshold",
            len(losses_list),
            player_moves_seen,
        )
    return None
