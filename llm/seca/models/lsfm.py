# seca/models/lsfm.py
import torch
import torch.nn as nn


class GameEncoder(nn.Module):
    def __init__(self, input_dim=64, d_model=128):
        super().__init__()
        self.embed = nn.Linear(input_dim, d_model)

    def forward(self, x):
        return self.embed(x)


class LSFM(nn.Module):
    def __init__(self, input_dim=64, d_model=128, layers=4, heads=4):
        super().__init__()

        self.game_encoder = GameEncoder(input_dim, d_model)

        encoder_layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=heads, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, layers)

        self.pool = nn.AdaptiveAvgPool1d(1)

        # heads
        self.rating_head = nn.Linear(d_model, 1)
        self.conf_head = nn.Linear(d_model, 1)
        self.weakness_head = nn.Linear(d_model, 8)
        self.learning_head = nn.Linear(d_model, 1)

    def forward(self, games):
        """
        games: (B, T, input_dim)
        """
        x = self.game_encoder(games)
        x = self.transformer(x)

        # mean pool
        z = x.mean(dim=1)

        return {
            "z": z,
            "rating": self.rating_head(z),
            "confidence": torch.softplus(self.conf_head(z)),
            "weakness": torch.sigmoid(self.weakness_head(z)),
            "learning": self.learning_head(z),
        }
