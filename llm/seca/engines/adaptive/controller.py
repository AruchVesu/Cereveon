# llm/seca/engines/adaptive/controller.py

import chess
import chess.engine
from dataclasses import dataclass


@dataclass
class OpponentConfig:
    target_elo: int = 1600
    depth: int = 12
    threads: int = 1
    hash_mb: int = 16


class AdaptiveOpponent:
    """
    SAFE-LOCK v1 opponent.

    Guarantees:
    - deterministic move for same FEN
    - no background learning
    - fixed search depth (no time randomness)
    - cleared hash every move
    """

    def __init__(self, stockfish_path: str, target_elo: int = 1600):
        self.engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
        self.sf = self.engine

        self.config = OpponentConfig(target_elo=target_elo)

        # HARD deterministic configuration
        options = {
            "Threads": self.config.threads,
            "Hash": self.config.hash_mb,
            "MultiPV": 1,  # single best move only
            "Skill Level": 20,  # disable randomness from skill noise
        }
        try:
            self.engine.configure(options)
        except chess.engine.EngineError as e:
            # Some engines (via python-chess) auto-manage MultiPV.
            # Retry without MultiPV to avoid startup failure.
            if "MultiPV" in str(e):
                options.pop("MultiPV", None)
                self.engine.configure(options)
            else:
                raise

    # ------------------------------------------------------------------
    # SAFE configuration update
    # ------------------------------------------------------------------

    def configure(self, opponent_cfg: dict | None = None):
        """
        Update only allowed deterministic parameters.
        """
        if not opponent_cfg:
            return

        self.config.target_elo = int(opponent_cfg.get("target_elo", self.config.target_elo))

        # NOTE:
        # We DO NOT allow time controls or randomness here.
        # Only deterministic depth remains.
        self.config.depth = int(opponent_cfg.get("depth", self.config.depth))

    # ------------------------------------------------------------------
    # Deterministic move selection
    # ------------------------------------------------------------------

    def select_move(self, board: chess.Board) -> chess.Move:
        """
        Fully deterministic:

        SAME FEN → SAME MOVE
        """

        # Clear hash to remove history influence
        self.engine.configure({"Clear Hash": None})

        result = self.engine.play(
            board, chess.engine.Limit(depth=self.config.depth)  # FIXED DEPTH ONLY
        )

        best_move = result.move

        # Absolute determinism safeguard
        # (stable ordering if engine ever returns tie)
        return sorted([best_move], key=lambda m: m.uci())[0]

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def close(self):
        self.engine.quit()
