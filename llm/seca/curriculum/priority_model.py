from typing import List
from llm.seca.curriculum.types import Weakness


def compute_priority(weaknesses: List[Weakness]) -> List[Weakness]:
    """
    Rank weaknesses by:
    severity × confidence
    """

    return sorted(
        weaknesses,
        key=lambda w: w.severity * w.confidence,
        reverse=True,
    )
