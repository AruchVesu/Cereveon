from typing import List

from .timeline_types import SkillPoint

BASE_RATING = 800
SKILL_TO_ELO = 12.0  # scale factor


def skill_to_rating(skill: float) -> float:
    """
    Converts 0–100 skill → Elo-like number.
    """
    return BASE_RATING + skill * SKILL_TO_ELO


def apply_rating_curve(points: List[SkillPoint]) -> List[SkillPoint]:
    """
    Fills rating values in SkillPoint list.
    """
    for p in points:
        p.rating = skill_to_rating(p.skill)

    return points
