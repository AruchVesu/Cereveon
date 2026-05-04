import pickle
from pathlib import Path

from .meta_bandit import LinUCB

MODEL_PATH = Path("llm/seca/brain/meta/meta_bandit.pkl")


class MetaCoach:

    def __init__(self, n_features: int, actions: list[str]):
        self.bandit = LinUCB(n_features, actions)

    # -----------------------------------------------------

    @classmethod
    def load(cls, n_features: int, actions: list[str]):
        if MODEL_PATH.exists():
            with open(MODEL_PATH, "rb") as f:
                return pickle.load(f)
        return cls(n_features, actions)

    # -----------------------------------------------------

    def save(self):
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(self, f)

    # -----------------------------------------------------

    def choose_strategy(self, context):
        return self.bandit.select_action(context)

    # -----------------------------------------------------

    def learn(self, context, strategy, reward):
        self.bandit.update(strategy, context, reward)
        self.save()
