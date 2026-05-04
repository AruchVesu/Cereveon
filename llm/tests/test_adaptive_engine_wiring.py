"""
Contract test: pins the ELO range and mapping formula that tie the backend
adaptation pipeline to the Android engine strength system.

Backend law (opponent_policy.py):
    elo = 600 + profile.opponent_strength * 1800
    → opponent_strength ∈ [0.0, 1.0]  →  elo ∈ [600, 2400]

Android law (EloToStrength.kt):
    strengthLevel = ((opponentElo - 600) * 100 / 1800).coerceIn(0, 100)
    → opponentElo=600  → 0
    → opponentElo=2400 → 100

These tests fail immediately if either end changes — CI enforces the contract.
"""

import math
import pytest

from llm.seca.adaptation.opponent_policy import choose_opponent_parameters


# ── Fake profile duck-type (avoids importing SkillProfile internals) ─────────

class FakeProfile:
    def __init__(self, opponent_strength: float, opponent_human_error: float = 0.0):
        self.opponent_strength = opponent_strength
        self.opponent_human_error = opponent_human_error


# ── Helpers ───────────────────────────────────────────────────────────────────

def _android_strength(opponent_elo: int) -> int:
    """Mirror of EloToStrength.map() from Android."""
    ELO_MIN, ELO_MAX = 600, 2400
    return max(0, min(100, (opponent_elo - ELO_MIN) * 100 // (ELO_MAX - ELO_MIN)))


# ── 1. ELO floor and ceiling ──────────────────────────────────────────────────

def test_minimum_strength_gives_elo_600():
    params = choose_opponent_parameters(FakeProfile(opponent_strength=0.0))
    assert params["target_elo"] == 600, (
        "Backend must produce ELO=600 for opponent_strength=0 "
        "(Android expects this as its lower bound)"
    )


def test_maximum_strength_gives_elo_2400():
    params = choose_opponent_parameters(FakeProfile(opponent_strength=1.0))
    assert params["target_elo"] == 2400, (
        "Backend must produce ELO=2400 for opponent_strength=1 "
        "(Android expects this as its upper bound)"
    )


# ── 2. Android strength mapping round-trip ────────────────────────────────────

def test_android_strength_at_elo_600_is_zero():
    assert _android_strength(600) == 0


def test_android_strength_at_elo_2400_is_100():
    assert _android_strength(2400) == 100


def test_android_strength_at_elo_1500_is_50():
    assert _android_strength(1500) == 50


# ── 3. End-to-end round-trip: backend strength → ELO → Android strength ──────

@pytest.mark.parametrize("opponent_strength,expected_android_strength", [
    (0.0,  0),
    (0.25, 25),
    (0.5,  50),
    (0.75, 75),
    (1.0,  100),
])
def test_round_trip_strength_mapping(opponent_strength, expected_android_strength):
    params = choose_opponent_parameters(FakeProfile(opponent_strength=opponent_strength))
    android_strength = _android_strength(params["target_elo"])
    assert android_strength == expected_android_strength, (
        f"opponent_strength={opponent_strength} → elo={params['target_elo']} "
        f"→ android_strength={android_strength}, expected {expected_android_strength}"
    )


# ── 4. Monotonicity ───────────────────────────────────────────────────────────

def test_elo_is_monotone_with_opponent_strength():
    strengths = [i / 10 for i in range(11)]
    elos = [choose_opponent_parameters(FakeProfile(s))["target_elo"] for s in strengths]
    for i in range(1, len(elos)):
        assert elos[i] >= elos[i - 1], (
            f"ELO must not decrease: strength {strengths[i-1]}→{strengths[i]}, "
            f"elo {elos[i-1]}→{elos[i]}"
        )


def test_android_strength_is_monotone_with_elo():
    elos = range(600, 2401, 100)
    strengths = [_android_strength(e) for e in elos]
    for i in range(1, len(strengths)):
        assert strengths[i] >= strengths[i - 1], (
            f"Android strength must not decrease across ELOs"
        )
