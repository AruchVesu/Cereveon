from dataclasses import dataclass
from datetime import datetime
from typing import List


@dataclass
class GameRecord:
    date: datetime
    confidence: float
    acpl: float
    blunders: int
    result: float  # 1.0 win, 0.5 draw, 0.0 loss


@dataclass
class SkillPoint:
    date: datetime
    skill: float
    rating: float


@dataclass
class SkillTimeline:
    games: List[GameRecord]
    points: List[SkillPoint]
