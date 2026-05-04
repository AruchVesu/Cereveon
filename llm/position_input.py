from __future__ import annotations

import chess


def normalize_fen(fen: str | None) -> str | None:
    if fen is None:
        return None
    if fen.strip().lower() == "startpos":
        return chess.STARTING_FEN
    return fen


def normalize_moves(moves: list[str] | None) -> list[str]:
    if not moves:
        return []
    return [move.strip() for move in moves if move and move.strip()]


def build_board(
    *,
    fen: str | None = None,
    moves: list[str] | None = None,
) -> chess.Board:
    normalized_moves = normalize_moves(moves)
    if normalized_moves:
        board = chess.Board()
        for uci in normalized_moves:
            board.push_uci(uci)
        return board

    normalized_fen = normalize_fen(fen)
    if normalized_fen is None:
        return chess.Board()
    return chess.Board(normalized_fen)


def normalize_position(
    *,
    fen: str | None = None,
    moves: list[str] | None = None,
) -> tuple[str, list[str], chess.Board]:
    normalized_moves = normalize_moves(moves)
    board = build_board(fen=fen, moves=normalized_moves)
    return board.fen(), normalized_moves, board
