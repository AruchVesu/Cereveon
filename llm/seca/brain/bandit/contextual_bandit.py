import pickle
from pathlib import Path
import numpy as np
from collections import defaultdict

MODEL_PATH = Path("seca/brain/bandit/contextual_bandit.pkl")


class LinUCB:
    def __init__(self, n_features: int, alpha: float = 1.0):
        self.alpha = alpha
        self.n_features = n_features

        self.A = defaultdict(lambda: np.eye(n_features))
        self.b = defaultdict(lambda: np.zeros((n_features, 1)))

    def _ensure_dim(self, action: str):
        d = self.n_features
        if self.A[action].shape != (d, d):
            self.A[action] = np.eye(d)
        if self.b[action].shape != (d, 1):
            self.b[action] = np.zeros(d).reshape(-1, 1)

    # -----------------------------
    # Choose best action
    # -----------------------------
    def select(self, context: np.ndarray, actions: list[str]) -> str:
        if not actions:
            raise ValueError("actions list must not be empty")
        context = context.reshape(-1, 1)

        best_action = None
        best_score = -1e9

        for a in actions:
            self._ensure_dim(a)
            A_inv = np.linalg.pinv(self.A[a])
            theta = A_inv @ self.b[a]

            exploit = float((theta.T @ context).item())
            explore = self.alpha * np.sqrt(float((context.T @ A_inv @ context).item()))

            score = exploit + explore

            if score > best_score:
                best_score = score
                best_action = a

        return best_action

    # -----------------------------
    # Update after reward observed
    # -----------------------------
    def update(self, action: str, context: np.ndarray, reward: float):
        context = context.reshape(-1, 1)

        self._ensure_dim(action)
        self.A[action] += context @ context.T
        self.b[action] += reward * context


class ContextualBandit:
    def __init__(self, n_features: int = 3, alpha: float = 1.0, model_path: Path | None = None):
        self.n_features = n_features
        self.alpha = alpha
        self.model_path = model_path or MODEL_PATH
        self.bandit = LinUCB(n_features=self.n_features, alpha=self.alpha)

        if self.model_path.exists():
            try:
                with open(self.model_path, "rb") as f:
                    loaded = pickle.load(f)
                self.bandit = loaded.bandit
                self.n_features = loaded.n_features
                self.alpha = loaded.alpha
            except Exception:
                pass

    def update(self, context: np.ndarray, action_index: int, reward: float):
        action_key = str(action_index)
        self.bandit.update(action_key, context, reward)

    def save(self):
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self, f)
