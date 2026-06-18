"""Coordinate-FREE, plain-English description of a chess move for LLM grounding.

The coach must never emit chess notation in its REPLY (Atrium no-notation rule;
``MOVE_ALGEBRAIC_PATTERNS`` strips a bare ``<file><rank>`` like ``"f3"``).  An
earlier coordinate piece-list grounding (#247/#248, reverted in #249) made the
LLM accurate enough to echo ``"f3"``, tripping that validator and forcing the
deterministic fallback.  This helper instead hands the coach the move in a form
it can safely repeat — ``"advanced the f-pawn one square"``, ``"moved a
knight"``, ``"castled kingside"`` — so it reads accurately AND obeys the
no-notation rule.

Output NEVER contains a ``<file><rank>`` pair: pawns are named by file letter
only (``"f-pawn"`` — the ``-`` breaks the file/rank adjacency the validator
looks for), pieces by type.  ``test_move_phrase.py`` pins that the result never
matches ``MOVE_ALGEBRAIC_PATTERNS``.
"""

from __future__ import annotations

import chess

_PIECE_NAME = {
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


def describe_move_plain(fen_after: str, uci: str) -> str:
    """Return a coordinate-free phrase for the move ``uci`` that produced
    ``fen_after``, or ``""`` if either can't be parsed.

    ``fen_after`` is the position AFTER the move (what the pipeline holds); the
    moved piece is read from the destination square.  The phrase is suitable to
    drop straight into the prompt and for the LLM to echo verbatim.
    """
    if not fen_after or not uci or len(uci) < 4:
        return ""
    try:
        board = chess.Board() if fen_after.strip() == "startpos" else chess.Board(fen_after)
        move = chess.Move.from_uci(uci)
    except (ValueError, IndexError):
        return ""

    from_file = chess.square_name(move.from_square)[0]  # 'a'..'h' letter only
    to_file = chess.square_name(move.to_square)[0]

    # Promotion: the destination now holds the promoted piece; frame it as the
    # pawn that promoted so the coach can say "you promoted your a-pawn".
    if move.promotion:
        promo = _PIECE_NAME.get(move.promotion, "piece")
        return f"promoted the {from_file}-pawn to a {promo}"

    piece = board.piece_at(move.to_square)
    if piece is None:  # uci doesn't match fen_after (illegal / stale) — bail out
        return ""

    # Castling: the king travels two files.
    if piece.piece_type == chess.KING and abs(
        chess.square_file(move.to_square) - chess.square_file(move.from_square)
    ) == 2:
        side = "kingside" if move.to_square > move.from_square else "queenside"
        return f"castled {side}"

    if piece.piece_type == chess.PAWN:
        if from_file != to_file:
            # A pawn only changes file on a capture (incl. en passant).
            return f"captured with the {from_file}-pawn"
        squares = abs(chess.square_rank(move.to_square) - chess.square_rank(move.from_square))
        distance = "two squares" if squares == 2 else "one square"
        return f"advanced the {from_file}-pawn {distance}"

    return f"moved a {_PIECE_NAME.get(piece.piece_type, 'piece')}"
