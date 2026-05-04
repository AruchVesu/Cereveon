import numpy as np
from collections import defaultdict


class GlobalLinUCB:
    def __init__(self, n_features: int, alpha: float = 1.0):
        self.alpha = alpha
        self.n_features = n_features

        self.A = defaultdict(lambda: np.eye(n_features))
        self.b = defaultdict(lambda: np.zeros((n_features, 1)))

    # --------------------------------
    # Choose best action for context
    # --------------------------------
    def select(self, context: np.ndarray, actions: list[str]) -> str:
        if not actions:
            raise ValueError("actions list must not be empty")
        context = context.reshape(-1, 1)

        best_action = None
        best_score = -1e9

        for a in actions:
            A_inv = np.linalg.pinv(self.A[a])
            theta = A_inv @ self.b[a]

            exploit = float((theta.T @ context).item())
            explore = self.alpha * np.sqrt(float((context.T @ A_inv @ context).item()))

            score = exploit + explore

            if score > best_score:
                best_score = score
                best_action = a

        return best_action

    # --------------------------------
    # Learn from ANY player's reward
    # --------------------------------
    def update(self, action: str, context: np.ndarray, reward: float):
        context = context.reshape(-1, 1)

        self.A[action] += context @ context.T
        self.b[action] += reward * context
