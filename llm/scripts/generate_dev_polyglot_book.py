from __future__ import annotations

import argparse
import os

import chess
import chess.polyglot

BOOK_ENTRIES = [
    ([], "e2e4", 50),
    ([], "d2d4", 35),
    ([], "c2c4", 25),
    ([], "g1f3", 20),
    (["e2e4"], "e7e5", 50),
    (["e2e4"], "c7c5", 35),
    (["e2e4"], "e7e6", 20),
    (["e2e4"], "c7c6", 15),
    (["d2d4"], "d7d5", 45),
    (["d2d4"], "g8f6", 35),
    (["d2d4"], "e7e6", 15),
    (["c2c4"], "e7e5", 30),
    (["c2c4"], "g8f6", 25),
    (["g1f3"], "d7d5", 25),
    (["g1f3"], "g8f6", 25),
    (["e2e4", "e7e5"], "g1f3", 50),
    (["e2e4", "e7e5"], "f1c4", 25),
    (["e2e4", "e7e5", "g1f3"], "b8c6", 50),
    (["e2e4", "c7c5"], "g1f3", 45),
    (["e2e4", "c7c5"], "d2d4", 35),
    (["d2d4", "d7d5"], "c2c4", 45),
    (["d2d4", "g8f6"], "c2c4", 40),
]


def _encode_move(move: chess.Move) -> int:
    promotion_part = (move.promotion - 1) if move.promotion else 0
    return move.to_square | (move.from_square << 6) | (promotion_part << 12)


def _build_records() -> list[tuple[int, int, int, int]]:
    records: list[tuple[int, int, int, int]] = []
    for prefix_moves, move_uci, weight in BOOK_ENTRIES:
        board = chess.Board()
        for prefix_move in prefix_moves:
            board.push_uci(prefix_move)

        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal book move {move_uci} for position {board.fen()}")

        key = chess.polyglot.zobrist_hash(board)
        raw_move = _encode_move(move)
        records.append((key, raw_move, weight, 0))

    records.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a small local Polyglot dev book.")
    parser.add_argument(
        "--output",
        default=os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "books", "performance.bin"
        ),
        help="Output .bin path",
    )
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    with open(args.output, "wb") as handle:
        for record in _build_records():
            handle.write(chess.polyglot.ENTRY_STRUCT.pack(*record))

    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
