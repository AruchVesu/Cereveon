# seca/performance/confidence.py
from __future__ import annotations
from dataclasses import dataclass
from math import exp
from typing import List

# -------------------------------
# DATA STRUCTURES
# -------------------------------


@dataclass
class GamePerformance:
    moves: int
    blunders: int
    acpl: float  # average centipawn loss
    eval_series: List[float]  # evaluation per move
    missed_tactics: int
    total_tactics: int
    expected_score: float  # from Elo formula
    actual_score: float  # 1 / 0.5 / 0


# -------------------------------
# METRIC HELPERS
# -------------------------------


def _blunder_rate(p: GamePerformance) -> float:
    return p.blunders / max(1, p.moves)


def _swing(p: GamePerformance) -> float:
    if len(p.eval_series) < 2:
        return 0.0
    diffs = [abs(p.eval_series[i] - p.eval_series[i - 1]) for i in range(1, len(p.eval_series))]
    return sum(diffs) / len(diffs)


def _tactical_miss_ratio(p: GamePerformance) -> float:
    if p.total_tactics == 0:
        return 0.0
    return p.missed_tactics / p.total_tactics


def _surprise(p: GamePerformance) -> float:
    return abs(p.actual_score - p.expected_score)


# -------------------------------
# QUALITY NORMALIZATION
# -------------------------------


def _q_acpl(acpl: float) -> float:
    return exp(-acpl / 100)


def _q_blunder(rate: float) -> float:
    return exp(-5 * rate)


def _q_swing(swing: float) -> float:
    return exp(-swing / 150)


def _q_tactic(miss_ratio: float) -> float:
    return 1.0 - miss_ratio


def _bonus(surprise: float) -> float:
    return 0.5 + surprise


# -------------------------------
# MAIN CONFIDENCE FUNCTION
# -------------------------------


def compute_confidence(p: GamePerformance) -> float:
    """
    Returns confidence in range [0.5, 1.5]
    """

    br = _blunder_rate(p)
    sw = _swing(p)
    tm = _tactical_miss_ratio(p)
    sp = _surprise(p)

    q_acpl = _q_acpl(p.acpl)
    q_blunder = _q_blunder(br)
    q_swing = _q_swing(sw)
    q_tactic = _q_tactic(tm)
    bonus = _bonus(sp)

    quality = 0.30 * q_acpl + 0.25 * q_blunder + 0.15 * q_swing + 0.20 * q_tactic + 0.10 * bonus

    # clamp
    return max(0.5, min(1.5, quality))
