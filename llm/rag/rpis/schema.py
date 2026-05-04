from pydantic import BaseModel


class SkillState(BaseModel):
    mean: float
    uncertainty: float
    learning_rate: float


class PerformanceVector(BaseModel):
    blunder_rate: float
    tactic_accuracy: float
    avg_centipawn_loss: float
    puzzle_success: float


class Prediction(BaseModel):
    weeks: int
    projected_skill: float
