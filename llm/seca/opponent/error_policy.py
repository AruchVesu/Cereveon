import random


class NeuralErrorPolicy:
    def __init__(self, predictor):
        self.predictor = predictor

    def choose_error_class(self, fen: str, elo: int, complexity: float) -> str:
        probs = self.predictor.predict(fen, elo, complexity)

        r = random.random()
        cumulative = 0

        for k, p in probs.items():
            cumulative += p
            if r <= cumulative:
                return k

        return "best"
