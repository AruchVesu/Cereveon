"""
Adaptive Opponent Controller — Production Implementation
-------------------------------------------------------
This module computes adaptive engine parameters based on player state,
curriculum state, and game context. It is designed to integrate with:

- JNI chess engine wrapper (Android / C++ backend)
- SECA player modeling system
- Training curriculum engine

Author: SECA Architecture
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, Any

# ============================================================
# Data Models
# ============================================================


@dataclass
class PlayerState:
    rating: float
    tactical_skill: float
    positional_skill: float
    endgame_skill: float
    blunder_rate: float
    tilt_level: float
    confidence: float


@dataclass
class CurriculumState:
    training_focus: str | None = None  # "tactics" | "positional" | "endgame" | None


@dataclass
class GameContext:
    evaluation: float  # normalized engine eval in [-1, 1]
    move_number: int


@dataclass
class OpponentParams:
    depth: int
    blunder_prob: float
    eval_noise_sigma: float
    tactical_bias: float
    horizon: int
    think_time_scale: float
    psychological_tilt: float


# ============================================================
# Math Helpers
# ============================================================


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def normalize_rating(rating: float) -> float:
    """Maps Elo → [0,1] smoothly."""
    return sigmoid((rating - 1200) / 400)


# ============================================================
# Core Mathematics
# ============================================================


P_TARGET = 0.55
SIGMA_MAX = 80.0  # centipawns
K_DEPTH = 6.0


class AdaptiveOpponentController:
    """
    Main adaptive controller computing opponent parameters.
    """

    # --------------------------------------------------------
    # Strength gap calculation
    # --------------------------------------------------------

    @staticmethod
    def target_elo_delta() -> float:
        """Solve Elo logistic for desired win probability."""
        return 400 * math.log10((1 - P_TARGET) / P_TARGET)

    # --------------------------------------------------------
    # Depth model
    # --------------------------------------------------------

    @staticmethod
    def compute_depth(r_hat: float) -> int:
        base_depth = 4 + 16 * r_hat
        delta_norm = -AdaptiveOpponentController.target_elo_delta() / 400
        depth = base_depth - K_DEPTH * delta_norm
        return max(2, int(round(depth)))

    # --------------------------------------------------------
    # Blunder model
    # --------------------------------------------------------

    @staticmethod
    def base_blunder(r_hat: float) -> float:
        return 0.35 * (1 - r_hat) ** 2

    @staticmethod
    def compute_blunder_prob(r_hat: float, tilt: float, eval_norm: float) -> float:
        base = AdaptiveOpponentController.base_blunder(r_hat)
        tilt_factor = 1 + 1.5 * tilt
        pressure_factor = 1 + max(0.0, -eval_norm)
        return min(1.0, base * tilt_factor * pressure_factor)

    # --------------------------------------------------------
    # Noise model
    # --------------------------------------------------------

    @staticmethod
    def compute_noise_sigma(r_hat: float, tilt: float) -> float:
        return SIGMA_MAX * (1 - r_hat) * (1 + tilt)

    # --------------------------------------------------------
    # Tactical bias
    # --------------------------------------------------------

    @staticmethod
    def compute_tactical_bias(tactical_skill: float, tilt: float) -> float:
        weakness = 1 - tactical_skill
        return max(0.0, min(1.0, 0.5 + 0.4 * weakness - 0.2 * tilt))

    # --------------------------------------------------------
    # Horizon model
    # --------------------------------------------------------

    @staticmethod
    def compute_horizon(r_hat: float) -> int:
        return int(round(2 + 6 * r_hat))

    # --------------------------------------------------------
    # Time usage model
    # --------------------------------------------------------

    @staticmethod
    def compute_time_scale(r_hat: float, tilt: float) -> float:
        return (0.6 + 0.8 * r_hat) * (1 - 0.5 * tilt)

    # --------------------------------------------------------
    # Psychological tilt update
    # --------------------------------------------------------

    @staticmethod
    def update_psychological_tilt(prev_tilt: float, blunder_size: float) -> float:
        alpha = 0.4
        tilt = prev_tilt + alpha * blunder_size
        return max(0.0, min(1.0, tilt * 0.9))  # decay

    # --------------------------------------------------------
    # Curriculum coupling
    # --------------------------------------------------------

    @staticmethod
    def apply_curriculum(params: OpponentParams, curriculum: CurriculumState) -> OpponentParams:
        if curriculum.training_focus == "tactics":
            params.tactical_bias = min(1.0, params.tactical_bias + 0.2)
            params.eval_noise_sigma *= 1.1
            params.horizon = max(1, params.horizon - 1)

        elif curriculum.training_focus == "positional":
            params.tactical_bias *= 0.7
            params.eval_noise_sigma *= 0.9

        elif curriculum.training_focus == "endgame":
            params.horizon += 2
            params.eval_noise_sigma *= 0.8

        return params

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    @classmethod
    def compute(
        cls,
        player: PlayerState,
        curriculum: CurriculumState,
        context: GameContext,
    ) -> OpponentParams:
        """Main entry point."""

        r_hat = normalize_rating(player.rating)

        depth = cls.compute_depth(r_hat)
        blunder_prob = cls.compute_blunder_prob(r_hat, player.tilt_level, context.evaluation)
        sigma = cls.compute_noise_sigma(r_hat, player.tilt_level)
        tactical_bias = cls.compute_tactical_bias(player.tactical_skill, player.tilt_level)
        horizon = cls.compute_horizon(r_hat)
        time_scale = cls.compute_time_scale(r_hat, player.tilt_level)

        params = OpponentParams(
            depth=depth,
            blunder_prob=blunder_prob,
            eval_noise_sigma=sigma,
            tactical_bias=tactical_bias,
            horizon=horizon,
            think_time_scale=time_scale,
            psychological_tilt=player.tilt_level,
        )

        return cls.apply_curriculum(params, curriculum)


# ============================================================
# Engine Move Selection Helper
# ============================================================


def select_move_with_noise(moves: Dict[str, float], sigma: float, blunder_prob: float) -> str:
    """
    Given move → eval dictionary, apply stochastic humanization.
    """

    noisy_moves: Dict[str, float] = {}

    for move, score in moves.items():
        noise = random.gauss(0, sigma)
        noisy_moves[move] = score + noise

    # With probability of blunder → choose suboptimal move
    if random.random() < blunder_prob and len(noisy_moves) > 1:
        sorted_moves = sorted(noisy_moves.items(), key=lambda x: x[1])
        return random.choice(sorted_moves[:-1])[0]

    # Otherwise choose best noisy move
    return max(noisy_moves, key=noisy_moves.get)


# ============================================================
# Example usage (dev only)
# ============================================================


if __name__ == "__main__":
    player = PlayerState(
        rating=1200,
        tactical_skill=0.4,
        positional_skill=0.5,
        endgame_skill=0.3,
        blunder_rate=0.2,
        tilt_level=0.3,
        confidence=0.6,
    )

    curriculum = CurriculumState(training_focus="tactics")

    context = GameContext(evaluation=-0.2, move_number=15)

    params = AdaptiveOpponentController.compute(player, curriculum, context)

    print(params)

    fake_moves = {"e5": 20, "Nc6": 15, "d5": -30}

    chosen = select_move_with_noise(fake_moves, params.eval_noise_sigma, params.blunder_prob)

    print("Chosen move:", chosen)
