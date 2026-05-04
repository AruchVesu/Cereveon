from dataclasses import dataclass


@dataclass
class CoachingPolicy:
    explanation_style: str = "balanced"
    tactic_ratio: float = 0.6
    session_length_min: int = 30
    feedback_tone: str = "neutral"
