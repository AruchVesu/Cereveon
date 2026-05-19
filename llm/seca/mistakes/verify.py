"""Server-side verifier for ``POST /training/verify-replay``.

Given the FEN at a mistake position and the user's proposed move,
ask Stockfish: is this move within ``VERIFY_THRESHOLD_CP`` centipawns
of the engine's best?  That's the trust anchor for the Phase 3 XP
credit path — the Android replay sheet only calls
``POST /training/solve`` after this endpoint has signed off.

Centipawn loss math
-------------------
Pythons-chess ``info["score"]`` is a ``PovScore``.  Calling
``.pov(player_color).score(mate_score=10000)`` collapses it to a
signed integer from the *player's* perspective: positive when the
player is winning, negative when losing.  Comparing the eval after
the engine's best move against the eval after the user's move gives
us a directly-meaningful "you gave up N centipawns" number that
matches the loss shape ``pgn_accuracy`` uses to score blunders.

We deliberately do NOT trust the FEN's side-to-move alone for player
identity — every replay attempt is by *whoever the FEN says moves
next*.  The Android client is expected to send the same
``biggest_mistake.fen_before`` it received from ``/game/finish``, so
the side-to-move there is the player who originally erred.

Engine pool acquire pattern matches the inline ``/engine/eval``
handler in ``llm/server.py``: skip ``evaluate_position`` (it only
returns the score) and call ``analyse`` directly so we get both the
score AND the engine's best move in a single round-trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import chess
import chess.engine

from llm.seca.engines.stockfish.pool import StockfishEnginePool

logger = logging.getLogger(__name__)


# A move is "correct" if it gives up at most this many centipawns
# vs the engine's preferred line.  30 cp is the user-confirmed
# Phase-3 threshold (see PR description) — lenient enough that
# several reasonable moves count, strict enough that obvious
# inaccuracies fail.
VERIFY_THRESHOLD_CP: int = 30


# Per-position movetime budget.  200 ms matches /engine/eval's budget
# (server.py:1437) so verify-replay's latency feels the same as a
# normal engine probe to the user.
_VERIFY_MOVETIME_MS: int = 200


# Mate-score clamp matches the rest of the codebase (pgn_accuracy,
# engine_eval).  Keeps centipawn arithmetic finite when one branch
# leads to a forced mate.
_MATE_SCORE_CP: int = 10000


class VerifyError(ValueError):
    """Caller-facing validation failure (bad FEN or move).  The router
    translates this into a 400."""


class EngineUnavailable(RuntimeError):
    """Engine pool down or acquire timed out.  Router translates into a
    503 so the client can show a soft retry message instead of a
    cryptic 500."""


@dataclass(frozen=True)
class VerifyResult:
    is_correct: bool
    """True when the user's move gave up at most ``VERIFY_THRESHOLD_CP``
    centipawns vs the engine's best.  Maps 1:1 to whether the replay
    sheet should call ``POST /training/solve`` next."""

    engine_best_uci: str
    """The move Stockfish would have played.  Surfaced even on a
    correct attempt so the UI can offer a "Here's what the engine
    plays" peek without a second round-trip."""

    eval_loss_cp: int
    """Signed centipawn delta — ``e_best - e_user`` from the player's
    POV.  Positive when the user's move is worse than the engine's.
    Engine search noise can push this slightly negative (user found a
    move the engine slightly prefers on deeper search); we accept that
    as ``is_correct=True`` since the threshold check is one-sided."""


def verify_replay_move(
    fen: str,
    move_uci: str,
    engine_pool: StockfishEnginePool,
    *,
    threshold_cp: int = VERIFY_THRESHOLD_CP,
) -> VerifyResult:
    """Verify a single mistake-replay attempt against the engine.

    Raises ``VerifyError`` on caller mistakes (FEN can't be parsed,
    move isn't legal in that position, etc.) and ``EngineUnavailable``
    when the Stockfish pool can't service the request.  Both are
    distinct from "engine ran and the answer is no" — that returns a
    normal ``VerifyResult(is_correct=False, ...)``.
    """
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise VerifyError(f"invalid FEN: {exc}") from exc

    try:
        user_move = chess.Move.from_uci(move_uci)
    except (ValueError, chess.InvalidMoveError) as exc:
        raise VerifyError(f"invalid UCI move: {exc}") from exc

    if user_move not in board.legal_moves:
        raise VerifyError(f"move {move_uci} is not legal in the given position")

    player_color = board.turn

    engine = None
    try:
        try:
            # The engine pool's queue + release surface is the
            # documented integration contract for routes that need
            # both score AND best move in one analyse() round trip
            # (see /engine/eval in server.py for the canonical
            # example).  The "public" ``evaluate_position`` only
            # returns the score and would cost a second analyse() to
            # recover best_move.
            engine = engine_pool._engines.get(  # pylint: disable=protected-access
                timeout=max(0.001, engine_pool.settings.queue_timeout_ms / 1000.0)
            )
        except Exception as exc:  # queue.Empty or pool not started
            raise EngineUnavailable("engine pool acquire timed out") from exc

        limit = chess.engine.Limit(time=_VERIFY_MOVETIME_MS / 1000.0)

        # First analysis: the pre-move position.  ``info["score"]`` is
        # the eval the engine projects after playing its own best move
        # (and best play continues); ``info["pv"][0]`` is that best
        # move itself.
        try:
            best_info = engine.analyse(board, limit)
        except Exception as exc:  # noqa: BLE001 — pyrosetta/process boundary
            raise EngineUnavailable(f"engine analyse failed: {exc}") from exc

        engine_best_move = _extract_best_move(best_info)
        e_best = _score_from_pov(best_info, player_color)

        # Second analysis: position AFTER the user's move.  Now it's
        # the opponent to move; ``info["score"]`` is from their POV.
        # ``.pov(player_color)`` flips it back to the player's view.
        board.push(user_move)
        try:
            user_info = engine.analyse(board, limit)
        except Exception as exc:  # noqa: BLE001
            raise EngineUnavailable(f"engine analyse failed: {exc}") from exc
        finally:
            board.pop()

        e_user = _score_from_pov(user_info, player_color)
    finally:
        if engine is not None:
            # Releasing the engine back to the pool — same protected-
            # access exemption as the acquire above.
            engine_pool._release_engine(engine)  # pylint: disable=protected-access

    loss_cp = e_best - e_user
    return VerifyResult(
        is_correct=loss_cp <= threshold_cp,
        engine_best_uci=engine_best_move.uci() if engine_best_move is not None else "",
        eval_loss_cp=int(loss_cp),
    )


def _extract_best_move(info: Any) -> chess.Move | None:
    # Typed as ``Any`` because python-chess hands us a TypedDict
    # (``InfoDict``) whose declared keys don't include the literal
    # access pattern we use here; a precise alias would either invite
    # version-pinning drift or require a cast at every call site.
    pv = info.get("pv") if isinstance(info, dict) else None
    if not pv:
        return None
    try:
        return pv[0]
    except (IndexError, AttributeError):
        return None


def _score_from_pov(info: Any, color: chess.Color) -> int:
    """Project ``info["score"]`` to ``color``'s POV as a clamped int.

    Mate is collapsed to ``±_MATE_SCORE_CP`` so any non-mate eval is
    dominated and the centipawn arithmetic stays finite.  Missing
    score (engine returned no eval) collapses to 0 — a deliberately
    neutral fallback that won't bias the verifier toward "correct" or
    "wrong" on the rare degenerate case.
    """
    score_obj = info.get("score") if isinstance(info, dict) else None
    if score_obj is None:
        return 0
    pov_score = score_obj.pov(color)
    if pov_score.is_mate():
        mate_in = pov_score.mate() or 0
        return _MATE_SCORE_CP if mate_in > 0 else -_MATE_SCORE_CP
    return int(pov_score.score(mate_score=_MATE_SCORE_CP) or 0)
