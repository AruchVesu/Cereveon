import torch
import torch.nn as nn


class SkillDynamicsModel(nn.Module):
    """
    Predicts next skill vector given current skill and training action.
    """

    def __init__(self, skill_dim: int = 8, action_dim: int = 6, hidden: int = 128):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(skill_dim + action_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, skill_dim),
        )

    # ------------------------------------------------

    def forward(self, skill, action):
        x = torch.cat([skill, action], dim=-1)
        delta = self.net(x)

        # residual update → stabilizes training
        return skill + delta
