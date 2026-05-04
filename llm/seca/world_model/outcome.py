import numpy as np


class OutcomePredictor:
    """
    Predicts win probability from player state.
    """

    def __init__(self, dim: int):
        self.w = np.random.randn(dim) * 0.1
        self.b = 0.0

    def predict_win_prob(self, state: np.ndarray) -> float:
        z = state @ self.w + self.b
        return 1 / (1 + np.exp(-z))
