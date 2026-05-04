# seca/inference/router.py

import chess
from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from llm.seca.inference.pipeline import explain_position

router = APIRouter()


class ExplainRequest(BaseModel):
    fen: str

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        stripped = v.strip()
        if stripped.lower() == "startpos":
            return v
        parts = stripped.split()
        if len(parts) != 6 or len(stripped) > 100:
            raise ValueError("invalid FEN")
        try:
            chess.Board(stripped)
        except ValueError:
            raise ValueError("invalid FEN")
        return v


@router.post("/explain")
async def explain(req: ExplainRequest):
    return await explain_position(req.fen)
