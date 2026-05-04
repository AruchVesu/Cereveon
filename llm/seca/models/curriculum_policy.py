# seca/models/curriculum_policy.py
import torch
import torch.nn as nn
import torch.nn.functional as F


class CurriculumPolicy(nn.Module):
    def __init__(self, z_dim=128, a_dim=32, hidden=256):
        super().__init__()

        self.policy = nn.Sequential(
            nn.Linear(z_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, a_dim),
        )

        self.value = nn.Sequential(
            nn.Linear(z_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, z):
        logits = self.policy(z)
        value = self.value(z)
        return logits, value
