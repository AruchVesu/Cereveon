import chess
import chess.engine
from typing import List


class StockfishTimeline:
    """
    Produces evaluation timeline + best-move comparison.
    """

    def __init__(self, engine_path: str, depth: int = 12):
        self.engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        self.depth = depth

    def close(self):
        self.engine.quit()

    def analyze_game(self, moves_san: List[str]):
        board = chess.Board()

        eval_series: List[float] = []
        acpl_sum = 0.0
        blunders = 0

        for san in moves_san:
            move = board.parse_san(san)

            # engine best move BEFORE playing
            info = self.engine.analyse(board, chess.engine.Limit(depth=self.depth))
            best_score = info["score"].white().score(mate_score=10000) or 0
            best_move = info.get("pv", [None])[0]

            # play user move
            board.push(move)

            # evaluate AFTER move
            info_after = self.engine.analyse(board, chess.engine.Limit(depth=self.depth))
            after_score = info_after["score"].white().score(mate_score=10000) or 0

            eval_series.append(after_score)

            # ACPL contribution
            acpl = abs(best_score - after_score)
            acpl_sum += acpl

            # blunder heuristic (≥ 200 cp loss)
            if acpl >= 200:
                blunders += 1

        moves = max(1, len(moves_san))
        avg_acpl = acpl_sum / moves

        return {
            "moves": moves,
            "blunders": blunders,
            "acpl": avg_acpl,
            "eval_series": eval_series,
        }
