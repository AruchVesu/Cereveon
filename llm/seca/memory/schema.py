from pydantic import BaseModel
from typing import List, Dict


class MistakePattern(BaseModel):
    tag: str
    count: int


class PlayerModel(BaseModel):
    skill_estimate: str
    common_mistakes: List[MistakePattern]
    strengths: List[str]


class SessionMemory(BaseModel):
    last_positions: List[str]
    last_topics: List[str]


class DialogueMemory(BaseModel):
    player: PlayerModel
    session: SessionMemory
