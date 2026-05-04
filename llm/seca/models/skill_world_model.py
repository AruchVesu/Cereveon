# seca/models/skill_world_model.py
import torch
import torch.nn as nn


class SkillDynamicsModel(nn.Module):
    """
    z_{t+1} = z_t + F(z_t, a_t)
    """

    def __init__(self, z_dim=128, a_dim=32, hidden=256):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(z_dim + a_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, z_dim),
        )

    def forward(self, z, a):
        """
        z: (B, z_dim)
        a: (B, a_dim)
        """
        delta = self.net(torch.cat([z, a], dim=-1))
        return z + delta, delta
