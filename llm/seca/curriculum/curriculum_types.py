from dataclasses import dataclass
from typing import List, Literal

LessonType = Literal[
    "tactics",
    "blunder_check",
    "endgame",
    "strategy",
    "calculation",
    "tilt_recovery",
]


@dataclass
class TrainingTask:
    type: LessonType
    title: str
    description: str
    difficulty: int  # 1–10


@dataclass
class CurriculumPlan:
    focus_skill: str
    tasks: List[TrainingTask]
    estimated_minutes: int
