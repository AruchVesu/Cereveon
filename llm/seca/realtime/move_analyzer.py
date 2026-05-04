class MoveAnalyzer:
    """
    Ultra-fast per-move evaluation.
    Must run in <50 ms for real-time UX.
    """

    def __init__(self, stockfish):
        self.sf = stockfish

    def evaluate(self, board, move):
        """
        Returns compact analysis signal.
        """

        info = self.sf.analyse(board, depth=10)

        best_eval = info["score"].white().score(mate_score=10000)

        board.push(move)
        after = self.sf.analyse(board, depth=10)
        after_eval = after["score"].white().score(mate_score=10000)
        board.pop()

        delta = after_eval - best_eval

        if delta < -200:
            quality = "blunder"
        elif delta < -80:
            quality = "mistake"
        elif delta < -30:
            quality = "inaccuracy"
        else:
            quality = "ok"

        return {
            "delta_cp": delta,
            "quality": quality,
        }
