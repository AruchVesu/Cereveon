from pathlib import Path

from llm.seca.analysis.pgn_loader import load_moves_from_pgn
from llm.seca.analysis.stockfish_timeline import StockfishTimeline
from llm.seca.analysis.performance_builder import build_game_performance
from llm.seca.performance.confidence import compute_confidence


class GameAnalyzer:
    """
    Full production pipeline:

    PGN → Stockfish → GamePerformance → Confidence
    """

    def __init__(self, stockfish_path: str, depth: int = 12):
        self.stockfish = StockfishTimeline(stockfish_path, depth)

    def close(self):
        self.stockfish.close()

    def analyze_pgn(
        self,
        pgn_path: str,
        *,
        expected_score: float,
        actual_score: float,
        missed_tactics: int = 0,
        total_tactics: int = 0,
    ):
        # 1. Load moves
        moves = load_moves_from_pgn(pgn_path)

        # 2. Engine timeline
        timeline = self.stockfish.analyze_game(moves)

        # 3. Build performance object
        performance = build_game_performance(
            timeline,
            expected_score=expected_score,
            actual_score=actual_score,
            missed_tactics=missed_tactics,
            total_tactics=total_tactics,
        )

        # 4. Compute confidence
        confidence = compute_confidence(performance)

        return {
            "performance": performance,
            "confidence": confidence,
        }
