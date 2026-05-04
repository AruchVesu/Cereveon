from typing import List
from statistics import mean

from .timeline_types import GameRecord, SkillPoint


def confidence_to_skill(conf: float) -> float:
    """
    Maps confidence (0–1) to latent skill scale (~0–100).
    """
    return conf * 100.0


def smooth_skill(values: List[float], window: int = 5) -> List[float]:
    """
    Moving average smoothing.
    """
    smoothed = []

    for i in range(len(values)):
        start = max(0, i - window + 1)
        segment = values[start : i + 1]
        smoothed.append(mean(segment))

    return smoothed


def build_skill_points(games: List[GameRecord]) -> List[SkillPoint]:
    if not games:
        return []

    raw_skill = [confidence_to_skill(g.confidence) for g in games]
    smooth = smooth_skill(raw_skill)

    # rating placeholder (filled later)
    return [SkillPoint(date=g.date, skill=s, rating=0.0) for g, s in zip(games, smooth)]
