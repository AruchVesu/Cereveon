# seca/henm/features.py
import chess
import numpy as np

PIECE_MAP = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 2,
    chess.ROOK: 3,
    chess.QUEEN: 4,
    chess.KING: 5,
}


def encode_board(fen: str) -> np.ndarray:
    board = chess.Board(fen)

    planes = np.zeros((12, 8, 8), dtype=np.float32)

    for square, piece in board.piece_map().items():
        color_offset = 0 if piece.color == chess.WHITE else 6
        piece_plane = PIECE_MAP[piece.piece_type] + color_offset

        r = 7 - chess.square_rank(square)
        c = chess.square_file(square)
        planes[piece_plane, r, c] = 1

    return planes.reshape(-1)


def encode_scalar_features(elo: int, complexity: float) -> np.ndarray:
    return np.array(
        [
            elo / 3000.0,
            complexity / 10.0,
        ],
        dtype=np.float32,
    )
