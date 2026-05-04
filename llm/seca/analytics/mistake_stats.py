"""
Mistake-pattern analytics: category classification and per-player aggregation.

All logic is purely deterministic — no model inference, no RL.

Schema
------
MistakeCategory  — string constants for the four training categories.
MistakeStats     — aggregated stats for a single player across N games.

Input format
------------
The aggregation entry point ``aggregate_from_weakness_dicts`` accepts a list of
weakness dicts, each produced by ``WeaknessVectorBuilder.build()``:

    {"opening": 0.12, "middlegame": 0.05, "endgame": 0.00}

Keys are game phases; values are mistake+blunder rates (count / total_moves).
Missing phases are treated as 0.0. Extra keys are ignored.

Category derivation
-------------------
Categories are derived deterministically from phase rates:

    opening   → OPENING_PREPARATION
    middlegame → POSITIONAL_PLAY (60 %), TACTICAL_VISION (40 %)
    endgame   → ENDGAME_TECHNIQUE

The split for middlegame reflects that blunders (tactical) and mistakes
(positional) both occur there. The 0.4 / 0.6 weights are fixed constants;
no learning or probability update happens here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class MistakeCategory:
    """String constants for the four training-focus categories."""

    OPENING_PREPARATION = "opening_preparation"
    TACTICAL_VISION = "tactical_vision"
    POSITIONAL_PLAY = "positional_play"
    ENDGAME_TECHNIQUE = "endgame_technique"

    ALL: tuple[str, ...] = (
        OPENING_PREPARATION,
        TACTICAL_VISION,
        POSITIONAL_PLAY,
        ENDGAME_TECHNIQUE,
    )


# ---------------------------------------------------------------------------
# Internal: phase → category mapping
# ---------------------------------------------------------------------------

# Each (phase, category) pair has a fixed contribution weight.
# Weights for categories that share a phase must be <= 1.0 each.
_PHASE_CATEGORY_WEIGHTS: dict[tuple[str, str], float] = {
    ("opening", MistakeCategory.OPENING_PREPARATION): 1.0,
    ("middlegame", MistakeCategory.POSITIONAL_PLAY): 0.6,
    ("middlegame", MistakeCategory.TACTICAL_VISION): 0.4,
    ("endgame", MistakeCategory.ENDGAME_TECHNIQUE): 1.0,
}

# Grouped by phase for quick lookup.
_PHASE_TO_CATEGORIES: dict[str, list[str]] = {
    "opening": [MistakeCategory.OPENING_PREPARATION],
    "middlegame": [MistakeCategory.POSITIONAL_PLAY, MistakeCategory.TACTICAL_VISION],
    "endgame": [MistakeCategory.ENDGAME_TECHNIQUE],
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MistakeStats:
    """
    Aggregated mistake statistics for a single player across ``games_analyzed``
    games.

    Attributes
    ----------
    games_analyzed : int
        Number of game records that were aggregated.
    phase_rates : dict[str, float]
        Average mistake+blunder rate per game phase.  Keys are phase names
        ("opening", "middlegame", "endgame"); values are floats in [0, 1].
    category_scores : dict[str, float]
        Derived score per ``MistakeCategory`` constant.  Score ∈ [0, 1].
        Higher score = more severe pattern in that category.
    dominant_category : str | None
        The ``MistakeCategory`` with the highest score, or ``None`` when no
        games have been analyzed.
    """

    games_analyzed: int
    phase_rates: dict[str, float] = field(default_factory=dict)
    category_scores: dict[str, float] = field(default_factory=dict)
    dominant_category: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _derive_category_scores(phase_rates: dict[str, float]) -> dict[str, float]:
    """
    Map phase-level rates to category scores using the fixed weight table.

    Each category score is the maximum weighted contribution across all phases
    that map to it.  Using max (rather than sum) prevents inflating a category
    when the same mistake pattern is observed in multiple phases.
    """
    scores: dict[str, float] = defaultdict(float)
    for phase, rate in phase_rates.items():
        for category in _PHASE_TO_CATEGORIES.get(phase, []):
            weight = _PHASE_CATEGORY_WEIGHTS.get((phase, category), 1.0)
            scores[category] = max(scores[category], rate * weight)
    return dict(scores)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def aggregate_from_weakness_dicts(weakness_dicts: list[dict]) -> MistakeStats:
    """
    Aggregate a list of per-game weakness dicts into a ``MistakeStats`` record.

    Parameters
    ----------
    weakness_dicts:
        Each element is the output of ``WeaknessVectorBuilder.build()``, i.e.
        ``{phase: rate}`` where phase ∈ {"opening", "middlegame", "endgame"}.
        Invalid values (negative, non-numeric) are silently skipped.
        An empty list produces a zero-games ``MistakeStats``.

    Returns
    -------
    MistakeStats
        Aggregated statistics.  ``games_analyzed`` reflects the number of
        dicts passed in, even if some dicts were empty.
    """
    if not weakness_dicts:
        return MistakeStats(games_analyzed=0)

    phase_sums: dict[str, float] = defaultdict(float)
    n = len(weakness_dicts)

    for wd in weakness_dicts:
        for phase, rate in wd.items():
            if isinstance(rate, (int, float)) and rate >= 0.0:
                phase_sums[phase] += float(rate)

    phase_rates = {phase: total / n for phase, total in phase_sums.items()}
    category_scores = _derive_category_scores(phase_rates)

    dominant: str | None = None
    if category_scores:
        dominant = max(category_scores, key=lambda c: category_scores[c])

    return MistakeStats(
        games_analyzed=n,
        phase_rates=phase_rates,
        category_scores=category_scores,
        dominant_category=dominant,
    )
