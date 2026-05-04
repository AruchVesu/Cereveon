from dataclasses import dataclass
from typing import List


@dataclass
class ExplanationPlan:
    summary: str
    key_points: List[str]
    consequence: str
    advice: str
