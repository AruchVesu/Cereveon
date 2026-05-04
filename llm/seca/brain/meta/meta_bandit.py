import numpy as np


class LinUCB:
    """
    Contextual bandit using Linear Upper Confidence Bound.
    """

    def __init__(self, n_features: int, actions: list[str], alpha: float = 1.0):
        self.actions = actions
        self.alpha = alpha
        self.n_features = n_features

        # Per-action parameters
        self.A = {a: np.eye(n_features) for a in actions}
        self.b = {a: np.zeros((n_features, 1)) for a in actions}

    # -----------------------------------------------------

    def _theta(self, action: str):
        return np.linalg.pinv(self.A[action]) @ self.b[action]

    # -----------------------------------------------------

    def select_action(self, x: np.ndarray) -> str:
        """
        Choose best strategy given context x.
        """
        if not self.actions:
            raise ValueError("actions list must not be empty")
        x = x.reshape(-1, 1)

        best_action = None
        best_score = -np.inf

        for a in self.actions:
            A_inv = np.linalg.pinv(self.A[a])
            theta = self._theta(a)

            # predicted reward
            mean = float((theta.T @ x).item())

            # uncertainty bonus
            bonus = self.alpha * np.sqrt(float((x.T @ A_inv @ x).item()))

            score = mean + bonus

            if score > best_score:
                best_score = score
                best_action = a

        return best_action

    # -----------------------------------------------------

    def update(self, action: str, x: np.ndarray, reward: float):
        """
        Learn from observed improvement.
        """
        x = x.reshape(-1, 1)

        self.A[action] += x @ x.T
        self.b[action] += reward * x
