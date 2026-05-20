"""Pick the first mistake the player made in a finished game.

Given the ``AccuracyAnalysis`` already produced at /game/finish time
(which carries per-player-move centipawn-loss numbers in
``losses_cp``), walk the PGN in move order and return the FIRST
position whose loss clears ``MIN_MISTAKE_LOSS_CP``.  Return a
``FirstMistake`` describing:

* the FEN the player was looking at when they erred,
* the move they actually played (UCI),
* the move number they'll see in the replay sheet header,
* the eval loss (centipawns).

Pedagogical rationale for "first" rather than "largest"
-------------------------------------------------------
A losing game often contains a single originating blunder followed by
several larger-centipawn-loss moves that are downstream symptoms — you
hung a piece on move 14, then on move 22 you tried to save the
position and gave up the exchange.  The 22-cp swing is bigger, but
the 14-cp swing is the lesson.  Surfacing the first above-threshold
loss teaches the user to avoid the root error before it cascades.

This is also the reason the detector intentionally does NOT compute
the engine's preferred move here.  That comes later, on the verify
path: the replay sheet shows the position + the user's bad move, lets
them try a new one, and the ``POST /training/verify-replay`` endpoint
compares the attempt against the engine's best move at that point.
Keeping the detector engine-call-free at /game/finish time means
mistake extraction is essentially free piggy-backed on the accuracy
recompute the route already runs.

A "mistake" worth replaying is a loss >= ``MIN_MISTAKE_LOSS_CP``
centipawns.  Smaller losses (inaccuracies) are not surfaced because
the replay UI's point is to teach the user a lesson on a clear
blunder, not to second-guess a borderline move.  When no move clears
the threshold (a clean game), the detector returns ``None`` and the
caller omits the ``biggest_mistake`` field from the response.

Wire-name note
--------------
The /game/finish response field is still ``biggest_mistake`` for
backward compatibility with the Android client decoded shape.  The
field name is a historical artifact of PR #192's original "pick the
worst loss" picker; the wire contract was kept stable when the
selection policy flipped to "first above threshold" so old client
builds keep decoding.  Internal Python identifiers (``FirstMistake``,
``find_first_mistake``) reflect the actual semantics.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import chess
import chess.pgn

logger = logging.getLogger(__name__)


# Floor on what counts as a "mistake" worth surfacing for replay.
# Aligned with ``llm.seca.analysis.pgn_accuracy._MISTAKE_THRESHOLD_CP``
# (150 cp) so the replay UI only fires on a real mistake (mistake or
# blunder severity), not on a borderline inaccuracy that the user
# could reasonably argue was fine.
MIN_MISTAKE_LOSS_CP: int = 150


@dataclass(frozen=True)
class FirstMistake:
    """One identified mistake-replay target — the player's first move
    whose centipawn loss cleared ``MIN_MISTAKE_LOSS_CP`` in PGN order.
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
    Always >= ``MIN_MISTAKE_LOSS_CP`` (otherwise the detector would
    have returned None).  Surfaced on the replay sheet so the user
    knows how big the hole was."""


def find_first_mistake(
    pgn_text: str,
    losses_cp: tuple[int, ...] | list[int],
    player_color: chess.Color,
) -> FirstMistake | None:
    """Return the FIRST mistake the player made in ``pgn_text``.

    Walks the PGN in move order and stops at the first player move
    whose corresponding entry in ``losses_cp`` is at least
    ``MIN_MISTAKE_LOSS_CP``.  Later, larger-loss moves are ignored
    deliberately — see the module docstring for the pedagogical reason.

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
    player_color:
        Which side the analysis was attributed to.  Used to skip
        opponent moves while walking the PGN.

    Returns
    -------
    ``FirstMistake`` describing the first player move whose loss
    clears the threshold, or ``None`` when no move clears it (a clean
    game, an empty PGN, or losses_cp / PGN drift).

    Errors
    ------
    Malformed PGN or per-move losses that don't line up with the
    PGN's player-move count both return ``None`` rather than raising
    — /game/finish must not 500 because the detector mis-counted; the
    caller falls back to omitting the field.
    """
    if not losses_cp:
        return None

    losses_list = list(losses_cp)

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
            if loss >= MIN_MISTAKE_LOSS_CP:
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

    # Walked the entire PGN without finding a player move whose loss
    # cleared the threshold.  Two reasons we land here:
    #   1. Clean game — every player loss < MIN_MISTAKE_LOSS_CP.
    #   2. losses_cp / PGN drift — losses_cp claimed more player
    #      moves than the PGN actually contains, and the only
    #      above-threshold entries lived past the PGN's last move.
    # Both collapse to the same caller-visible answer ("no mistake
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
