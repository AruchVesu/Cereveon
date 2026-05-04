"""
Deterministic training recommendation engine.

Produces a ranked list of ``TrainingFocus`` items from a ``MistakeStats``
record using fixed threshold rules.  No model inference, no RL, no
probability updates — all logic is a static lookup table.

Recommendation rules
--------------------
Each ``MistakeCategory`` has a threshold score above which a ``TrainingFocus``
is emitted.  Priority is determined by how far the observed score exceeds the
threshold:

    score >= 2× threshold  → "high"
    score >= 1.25× threshold → "medium"
    score >= threshold     → "low"

Results are sorted high → medium → low, then by score descending within the
same priority tier.  This ordering is deterministic for identical inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mistake_stats import MistakeCategory, MistakeStats

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingFocus:
    """
    A single training recommendation.

    Attributes
    ----------
    category : str
        One of the ``MistakeCategory`` constants.
    priority : str
        "high", "medium", or "low".
    rationale : str
        Human-readable explanation of why this focus area was selected.
    """

    category: str
    priority: str
    rationale: str


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

# (threshold_score, rationale_text) keyed by MistakeCategory constant.
# Threshold values are rates (fraction of moves that were mistakes/blunders)
# mapped through the phase-weight table in mistake_stats.py.
_CATEGORY_RULES: dict[str, tuple[float, str]] = {
    MistakeCategory.OPENING_PREPARATION: (
        0.08,
        "Frequent errors in the opening phase suggest studying key opening "
        "principles, typical pawn structures, and move-order theory for your "
        "most-played openings.",
    ),
    MistakeCategory.TACTICAL_VISION: (
        0.04,
        "A high blunder rate indicates that tactical patterns are being missed. "
        "Daily tactical puzzle practice — forks, pins, skewers, and back-rank "
        "mates — will sharpen pattern recognition.",
    ),
    MistakeCategory.POSITIONAL_PLAY: (
        0.05,
        "Positional mistakes in the middlegame suggest training on pawn "
        "structure evaluation, piece activity, weak squares, and long-term "
        "planning concepts.",
    ),
    MistakeCategory.ENDGAME_TECHNIQUE: (
        0.06,
        "Errors in the endgame phase indicate gaps in technique. Focus on "
        "fundamental K+P endings, rook endings, and the principle of "
        "king activity in simplified positions.",
    ),
}

_PRIORITY_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _priority_from_ratio(ratio: float) -> str:
    """
    Convert (score / threshold) ratio to a priority label.

    The ratio is guaranteed to be >= 1.0 (caller only passes when score >=
    threshold).
    """
    if ratio >= 2.0:
        return "high"
    if ratio >= 1.25:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_training_recommendations(stats: MistakeStats) -> list[TrainingFocus]:
    """
    Produce a deterministic, ranked list of training focus areas.

    A ``TrainingFocus`` is emitted for every category whose score in
    ``stats.category_scores`` meets or exceeds the category's threshold.
    Categories that fall below the threshold are silently omitted.

    Parameters
    ----------
    stats:
        Aggregated mistake statistics from ``aggregate_from_weakness_dicts``.
        If ``games_analyzed == 0`` or all category scores are below their
        thresholds, an empty list is returned.

    Returns
    -------
    list[TrainingFocus]
        Sorted by priority (high first) then by score descending within the
        same priority tier.  The ordering is fully deterministic.
    """
    recommendations: list[TrainingFocus] = []

    for category, (threshold, rationale) in _CATEGORY_RULES.items():
        score = stats.category_scores.get(category, 0.0)
        if score >= threshold:
            ratio = score / threshold
            recommendations.append(
                TrainingFocus(
                    category=category,
                    priority=_priority_from_ratio(ratio),
                    rationale=rationale,
                )
            )

    # Stable sort: primary key = priority tier, secondary = score descending.
    recommendations.sort(
        key=lambda f: (
            _PRIORITY_ORDER[f.priority],
            -stats.category_scores.get(f.category, 0.0),
        )
    )
    return recommendations
