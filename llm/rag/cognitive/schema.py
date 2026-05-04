from pydantic import BaseModel


class CognitiveStyle(BaseModel):
    calculation_style: str
    risk_profile: str
    learning_style: str
    stability: str


class TiltState(BaseModel):
    state: str
    confidence: float


class MentalSnapshot(BaseModel):
    style: CognitiveStyle
    tilt: TiltState


def detect_tilt(last_moves):
    blunders = sum(1 for m in last_moves if m == "blunder")

    if blunders >= 3:
        return "tilted"
    if blunders == 2:
        return "unstable"
    return "calm"
