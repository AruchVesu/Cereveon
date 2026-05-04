import chess

from .pgn_loader import load_game
from .stockfish_eval import StockfishEvaluator
from .mistake_classifier import classify_delta
from .weakness_vector import WeaknessVectorBuilder


class GameWeaknessAnalyzer:
    """
    Converts a PGN into a structured weakness vector.
    """

    def __init__(self, stockfish_path: str):
        self.sf = StockfishEvaluator(stockfish_path)

    # ------------------------------------------------

    def _phase(self, board: chess.Board) -> str:
        """
        Simple phase detection.
        """
        pieces = len(board.piece_map())

        if pieces > 24:
            return "opening"
        if pieces > 12:
            return "middlegame"
        return "endgame"

    # ------------------------------------------------

    def analyze(self, pgn_text: str) -> dict:

        game = load_game(pgn_text)
        board = game.board()

        builder = WeaknessVectorBuilder()

        prev_eval = self.sf.evaluate(board)

        for move in game.mainline_moves():
            phase = self._phase(board)

            board.push(move)
            new_eval = self.sf.evaluate(board)

            delta = new_eval - prev_eval
            mistake = classify_delta(delta)

            builder.record(phase, mistake)

            prev_eval = new_eval

        return builder.build()

    # ------------------------------------------------

    def close(self):
        self.sf.close()
