# seca/henm/infer.py
import torch

from .model import HumanErrorNet
from .features import encode_board, encode_scalar_features


class HumanErrorPredictor:
    def __init__(self, model_path: str, input_dim: int):
        self.model = HumanErrorNet(input_dim)
        self.model.load_state_dict(torch.load(model_path, map_location="cpu"))
        self.model.eval()

    @torch.no_grad()
    def predict(self, fen: str, elo: int, complexity: float):
        x = encode_board(fen)
        s = encode_scalar_features(elo, complexity)

        vec = torch.tensor(list(x) + list(s)).float().unsqueeze(0)

        logits = self.model(vec)
        probs = torch.softmax(logits, dim=1)[0]

        return {
            "best": float(probs[0]),
            "inaccuracy": float(probs[1]),
            "mistake": float(probs[2]),
            "blunder": float(probs[3]),
        }
