import chess
import chess.engine
from pathlib import Path


class StockfishAdapter:
    def __init__(self, engine_path: str, depth: int = 12):
        self.engine_path = Path(engine_path)
        self.depth = depth
        self.engine = chess.engine.SimpleEngine.popen_uci(str(self.engine_path))

    def best_move(self, board: chess.Board) -> chess.Move:
        result = self.engine.play(board, chess.engine.Limit(depth=self.depth))
        return result.move

    def evaluate(self, board: chess.Board) -> dict:
        info = self.engine.analyse(board, chess.engine.Limit(depth=self.depth))

        score = info["score"].pov(board.turn)

        if score.is_mate():
            return {"type": "mate", "value": score.mate()}
        else:
            return {"type": "cp", "value": score.score()}

    def close(self):
        self.engine.quit()
