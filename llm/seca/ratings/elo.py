# seca/ratings/elo.py
from __future__ import annotations
from dataclasses import dataclass

# -------------------------------
# CONFIG
# -------------------------------


def k_factor(rating: int) -> int:
    """Stage-adaptive K."""
    if rating < 1200:
        return 40
    if rating < 1800:
        return 24
    return 16


# -------------------------------
# CORE MATH
# -------------------------------


def expected_score(r_a: float, r_b: float) -> float:
    """Logistic Elo expectation."""
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400))


def update_rating(
    rating: float,
    opponent_rating: float,
    score: float,
    confidence: float = 1.0,
) -> int:
    """
    SECA Elo update.

    confidence:
        0.5  -> weak performance
        1.0  -> normal
        1.5  -> exceptional learning signal
    """
    e = expected_score(rating, opponent_rating)
    k = k_factor(int(rating))

    effective_k = k * confidence
    new_rating = rating + effective_k * (score - e)

    return round(new_rating)


# -------------------------------
# RESULT ENUM
# -------------------------------


@dataclass
class GameResult:
    score: float  # 1 / 0.5 / 0
    opponent_rating: int
    confidence: float = 1.0


def apply_result(current_rating: int, result: GameResult) -> int:
    """Convenience wrapper."""
    return update_rating(
        rating=current_rating,
        opponent_rating=result.opponent_rating,
        score=result.score,
        confidence=result.confidence,
    )
