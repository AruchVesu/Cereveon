from dataclasses import dataclass
from typing import Literal

OutcomeType = Literal["improved", "neutral", "worse"]


@dataclass
class ExplanationOutcome:
    player_id: str
    outcome: OutcomeType
    mistake_before: bool
    mistake_after: bool
