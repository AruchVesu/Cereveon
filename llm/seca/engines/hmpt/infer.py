# seca/engines/hmpt/infer.py

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

import torch
import torch.nn as nn
import chess

# --------------------------------------------------
# Minimal placeholder neural model
# --------------------------------------------------


class _StubHMPT(nn.Module):
    """
    Temporary untrained network.
    Exists only so loading / inference pipeline works.
    """

    def __init__(self):
        super().__init__()
        self.dummy = nn.Linear(1, 1)

    def forward(self, x):
        return self.dummy(x)


# --------------------------------------------------
# Main inference interface used by SECA
# --------------------------------------------------


class HMPTInference:
    """
    Production-facing HMPT wrapper.

    Guarantees:
    - Always returns a legal move
    - Never crashes AdaptiveOpponent
    - Works with or without trained weights
    """

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = Path(model_path) if model_path else None
        self.device = torch.device("cpu")

        self.model: nn.Module = _StubHMPT().to(self.device)
        self.model.eval()

        # Try loading real weights if provided
        if self.model_path and self.model_path.exists():
            try:
                state = torch.load(self.model_path, map_location=self.device)
                self.model.load_state_dict(state)
                logger.info("HMPT loaded weights from %s", self.model_path)
            except Exception as e:
                logger.warning("HMPT failed to load weights from %s, using stub: %s", self.model_path, e)

    # --------------------------------------------------
    # Core public API
    # --------------------------------------------------

    def sample_move(
        self,
        board: chess.Board,
        rating: int = 1500,
        phase: str = "middlegame",
        time_control: str = "rapid",
    ) -> chess.Move:
        """
        Returns a legal chess move.

        v0 behavior:
        → purely random legal move
        → deterministic safety
        """

        legal_moves = list(board.legal_moves)

        if not legal_moves:
            raise ValueError("No legal moves available (game over).")

        # --- FUTURE ---
        # Here HMPT logits → softmax → sampling
        # For now: random move
        return random.choice(legal_moves)


# --------------------------------------------------
# Convenience factory (used by AdaptiveOpponent)
# --------------------------------------------------


def load_hmpt(model_path: Optional[str] = None) -> HMPTInference:
    """
    Clean constructor for engine integration.
    """
    return HMPTInference(model_path=model_path)
