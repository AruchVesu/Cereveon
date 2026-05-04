import chess
import chess.engine


class StockfishEvaluator:
    def __init__(self, path: str, depth: int = 12):
        self.engine = chess.engine.SimpleEngine.popen_uci(path)
        self.depth = depth

    # ------------------------------------------------

    def evaluate(self, board: chess.Board) -> float:
        """
        Returns centipawn evaluation from side to move perspective.
        """
        info = self.engine.analyse(board, chess.engine.Limit(depth=self.depth))
        score = info["score"].pov(board.turn)

        if score.is_mate():
            return 10000 if score.mate() > 0 else -10000

        return score.score()

    # ------------------------------------------------

    def close(self):
        self.engine.quit()
