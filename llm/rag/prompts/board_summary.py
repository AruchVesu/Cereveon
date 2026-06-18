"""Deterministic, human-readable board description for LLM grounding.

The Mode-1 / Mode-2 prompts historically handed the model only the raw FEN
string and expected it to parse the piece placement itself.  LLMs do this
unreliably — they hallucinate which piece sits where (the in-app report:
the coach said the player moved the king's pawn when they had moved the
f-pawn).  ``describe_position`` converts the FEN into an explicit piece list
(via python-chess, the same engine-truth source the rest of the pipeline
uses) so the model never has to read FEN.  It is purely additive grounding —
the raw FEN stays in the prompt as well.
"""

from __future__ import annotations

import chess

_PIECE_ORDER = (
    (chess.KING, "King"),
    (chess.QUEEN, "Queen"),
    (chess.ROOK, "Rooks"),
    (chess.BISHOP, "Bishops"),
    (chess.KNIGHT, "Knights"),
    (chess.PAWN, "Pawns"),
)


def describe_position(fen: str | None) -> str:
    """Return a plain-language piece list + side to move, or "" if unparseable.

    Deterministic: squares are emitted in sorted order, so the rendered prompt
    stays byte-identical for identical inputs.  On any parse failure the caller
    still has the raw FEN in the prompt, so returning "" degrades gracefully.
    """
    if not fen:
        return ""
    try:
        board = chess.Board() if fen.strip() == "startpos" else chess.Board(fen)
    except Exception:  # noqa: BLE001 — malformed FEN must not break prompt assembly
        return ""

    side = "White" if board.turn == chess.WHITE else "Black"
    lines = [f"Side to move: {side}."]
    for color, label in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
        segments: list[str] = []
        for piece_type, name in _PIECE_ORDER:
            squares = sorted(chess.square_name(sq) for sq in board.pieces(piece_type, color))
            if squares:
                segments.append(f"{name} {' '.join(squares)}")
        if segments:
            lines.append(f"{label}: " + "; ".join(segments))
    return "\n".join(lines)
