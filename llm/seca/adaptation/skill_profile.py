from dataclasses import dataclass


@dataclass
class SkillProfile:
    rating: float
    confidence: float

    explanation_depth: float  # 0–1
    concept_complexity: float  # 0–1
    opponent_strength: float  # 0–1
    opponent_human_error: float  # 0–1


def build_skill_profile(rating: float, confidence: float) -> SkillProfile:
    """
    Smooth mapping from rating → pedagogy parameters.
    """

    # normalize rating to 0–1 range (400–2400)
    r = max(0.0, min(1.0, (rating - 400) / 2000))

    return SkillProfile(
        rating=rating,
        confidence=confidence,
        explanation_depth=r,
        concept_complexity=r**1.2,
        opponent_strength=r,
        opponent_human_error=1.0 - r,
    )
