# seca/henm/model.py
import torch
import torch.nn as nn


class HumanErrorNet(nn.Module):
    """
    Predicts mistake class from position + rating features.
    """

    def __init__(self, input_dim: int):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(input_dim, 512),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(512, 256),
            nn.ReLU(),
            nn.Linear(256, 4),  # best / inaccuracy / mistake / blunder
        )

    def forward(self, x):
        return self.net(x)
