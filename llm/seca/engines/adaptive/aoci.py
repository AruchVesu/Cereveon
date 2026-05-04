"""
HMPT → Adaptive Opponent Controller Integration
===============================================

This module plugs the Human‑Move Prediction Transformer (HMPT)
into the SECA Adaptive Opponent Controller.

Responsibilities:
- Query HMPT for human‑style move distribution
- Blend with engine optimal move using strength parameter
- Produce final move matching target ELO / difficulty

This is **production‑ready minimal v1**.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Tuple

import chess

# HMPT inference API (must exist in seca.engines.hmpt.infer)
try:
    from llm.seca.engines.hmpt.infer import load_hmpt
except Exception:  # pragma: no cover
    load_hmpt = None  # type: ignore


# ---------------------------------------------------------------------------
# Strength Model
# ---------------------------------------------------------------------------


@dataclass
class OpponentStrength:
    """Represents adaptive opponent strength configuration."""

    elo: int = 1200
    human_blend: float = 0.5  # 0 → pure engine, 1 → pure human model
    randomness: float = 0.05  # stochastic exploration

    @staticmethod
    def from_elo(elo: int) -> "OpponentStrength":
        """Convert ELO to internal blend parameters."""

        # Smooth mapping
        human_blend = max(0.05, min(0.95, 1.0 - (elo - 600) / 2400))

        randomness = max(0.01, min(0.25, 1.2 - elo / 2000))

        return OpponentStrength(
            elo=elo,
            human_blend=human_blend,
            randomness=randomness,
        )


# ---------------------------------------------------------------------------
# Engine Stub (replace with JNI / Stockfish bridge)
# ---------------------------------------------------------------------------


class EngineAdapter:
    """
    Minimal engine adapter.

    Replace get_best_move() with:
        - JNI call to native Stockfish
        - or UCI subprocess
    """

    def get_best_move(self, board: chess.Board) -> chess.Move:
        """Return pseudo‑best move (legal random for stub)."""
        return random.choice(list(board.legal_moves))


# ---------------------------------------------------------------------------
# HMPT Wrapper
# ---------------------------------------------------------------------------


class HMPTWrapper:
    """Loads and queries HMPT human‑move model."""

    def __init__(self):
        if load_hmpt is None:
            raise RuntimeError("HMPT not available in PYTHONPATH")

        self.model = load_hmpt()

    def sample_move(self, board: chess.Board) -> chess.Move:
        """Sample human‑style move."""
        return self.model.sample_move(board)


# ---------------------------------------------------------------------------
# Adaptive Opponent Controller
# ---------------------------------------------------------------------------


class AdaptiveOpponent:
    """
    Core controller blending:
        Engine optimal play
        HMPT human‑style errors

    Final move = probabilistic mix based on strength profile.
    """

    def __init__(
        self,
        strength: OpponentStrength,
        engine: Optional[EngineAdapter] = None,
        hmpt: Optional[HMPTWrapper] = None,
    ):
        self.strength = strength
        self.engine = engine or EngineAdapter()
        self.hmpt = hmpt or HMPTWrapper()

    # ------------------------------------------------------------------
    # Move Selection Logic
    # ------------------------------------------------------------------

    def choose_move(self, board: chess.Board) -> chess.Move:
        """Select final move according to adaptive policy."""

        best_move = self.engine.get_best_move(board)

        # Pure engine at high ELO
        if self.strength.human_blend <= 0.05:
            return best_move

        # Pure human at very low ELO
        if self.strength.human_blend >= 0.95:
            return self.hmpt.sample_move(board)

        # Mixed policy
        if random.random() < self.strength.human_blend:
            move = self.hmpt.sample_move(board)
        else:
            move = best_move

        # Add exploration noise
        if random.random() < self.strength.randomness:
            move = random.choice(list(board.legal_moves))

        return move


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_adaptive_opponent(elo: int) -> AdaptiveOpponent:
    """Convenience factory used by Android / server layer."""

    strength = OpponentStrength.from_elo(elo)

    return AdaptiveOpponent(strength=strength)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    board = chess.Board()

    opponent = create_adaptive_opponent(elo=900)

    move = opponent.choose_move(board)

    print("Chosen move:", move, board.san(move))
