from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SkillVector:
    tactics: float
    strategy: float
    endgame: float
    calculation: float
    opening: float


@dataclass
class Weakness:
    name: str
    severity: float  # 0–1
    confidence: float  # reliability of estimate


@dataclass
class TrainingTask:
    topic: str
    difficulty: float
    format: str  # puzzle | explanation | game | drill
    expected_gain: float
