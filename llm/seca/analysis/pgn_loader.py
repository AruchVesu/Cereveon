import io

import chess
import chess.pgn


def load_game(pgn_text: str):
    """
    Returns a python-chess Game object parsed from a PGN string.
    """
    return chess.pgn.read_game(io.StringIO(pgn_text))


def load_moves_from_pgn(pgn_path: str) -> list[str]:
    """
    Read a PGN file from disk and return the mainline moves in SAN notation.

    Parameters
    ----------
    pgn_path:
        Absolute or relative path to a .pgn file.

    Returns
    -------
    list[str]
        Mainline moves in Standard Algebraic Notation, in play order.
        Returns an empty list when the file contains no game or no moves.

    Raises
    ------
    FileNotFoundError
        When ``pgn_path`` does not exist on the filesystem.
    """
    with open(pgn_path, encoding="utf-8", errors="replace") as fh:
        game = chess.pgn.read_game(fh)

    if game is None:
        return []

    board = game.board()
    moves: list[str] = []
    for move in game.mainline_moves():
        moves.append(board.san(move))
        board.push(move)

    return moves
