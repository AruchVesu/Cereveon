"""
Neural Skill Dynamics World Model
--------------------------------
Predicts how a player's latent chess skill evolves after training events.
Production‑ready PyTorch module for SECA.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

# ---------------------------------------------------------------------
# Skill State Encoder
# ---------------------------------------------------------------------


class SkillEncoder(nn.Module):
    """Encodes raw skill vector into latent representation."""

    def __init__(self, input_dim: int = 5, latent_dim: int = 32):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, latent_dim),
        )

    def forward(self, skill_vec: torch.Tensor) -> torch.Tensor:
        return self.net(skill_vec)


# ---------------------------------------------------------------------
# Training Event Encoder
# ---------------------------------------------------------------------


class EventEncoder(nn.Module):
    """Encodes training event metadata into latent space."""

    def __init__(self, event_dim: int = 6, latent_dim: int = 32):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(event_dim, 64),
            nn.ReLU(),
            nn.Linear(64, latent_dim),
        )

    def forward(self, event_vec: torch.Tensor) -> torch.Tensor:
        return self.net(event_vec)


# ---------------------------------------------------------------------
# Latent Dynamics Core (GRU world model)
# ---------------------------------------------------------------------


class SkillDynamicsCore(nn.Module):
    """Recurrent world model predicting latent skill evolution."""

    def __init__(self, latent_dim: int = 32):
        super().__init__()

        self.gru = nn.GRU(
            input_size=latent_dim * 2,
            hidden_size=latent_dim,
            batch_first=True,
        )

    def forward(self, latent_skill: torch.Tensor, latent_event: torch.Tensor):
        """
        Inputs:
            latent_skill: (B, 1, D)
            latent_event: (B, 1, D)
        """

        x = torch.cat([latent_skill, latent_event], dim=-1)
        out, _ = self.gru(x)
        return out


# ---------------------------------------------------------------------
# Skill Decoder
# ---------------------------------------------------------------------


class SkillDecoder(nn.Module):
    """Maps latent state back to observable skill vector."""

    def __init__(self, latent_dim: int = 32, output_dim: int = 5):
        super().__init__()

        self.net = nn.Sequential(
            nn.Linear(latent_dim, 64),
            nn.ReLU(),
            nn.Linear(64, output_dim),
        )

    def forward(self, latent: torch.Tensor) -> torch.Tensor:
        return self.net(latent)


# ---------------------------------------------------------------------
# Full Neural Skill World Model
# ---------------------------------------------------------------------


class NeuralSkillWorldModel(nn.Module):
    """
    End‑to‑end differentiable model:
        skill_t, event_t → skill_{t+1}
    """

    def __init__(self, skill_dim: int = 5, event_dim: int = 6, latent_dim: int = 32):
        super().__init__()

        self.skill_encoder = SkillEncoder(skill_dim, latent_dim)
        self.event_encoder = EventEncoder(event_dim, latent_dim)
        self.dynamics = SkillDynamicsCore(latent_dim)
        self.decoder = SkillDecoder(latent_dim, skill_dim)

    # ---------------------------------------------------------------

    def forward(self, skill_vec: torch.Tensor, event_vec: torch.Tensor):
        """
        Predict next skill vector.

        Shapes:
            skill_vec: (B, skill_dim)
            event_vec: (B, event_dim)
        """

        latent_skill = self.skill_encoder(skill_vec).unsqueeze(1)
        latent_event = self.event_encoder(event_vec).unsqueeze(1)

        latent_next = self.dynamics(latent_skill, latent_event)
        latent_next = latent_next.squeeze(1)

        next_skill = self.decoder(latent_next)
        return next_skill


# ---------------------------------------------------------------------
# Training Step Utility
# ---------------------------------------------------------------------


class SkillWorldModelTrainer:
    """Lightweight trainer wrapper for SECA offline learning."""

    def __init__(self, model: NeuralSkillWorldModel, lr: float = 1e-3):
        self.model = model
        self.opt = torch.optim.Adam(model.parameters(), lr=lr)

    # ---------------------------------------------------------------

    def step(self, skill, event, target_skill):
        """Single gradient update."""

        pred = self.model(skill, event)
        loss = F.mse_loss(pred, target_skill)

        self.opt.zero_grad()
        loss.backward()
        self.opt.step()

        return loss.item()


# ---------------------------------------------------------------------
# Inference Helper
# ---------------------------------------------------------------------


@torch.no_grad()
def predict_next_skill(model: NeuralSkillWorldModel, skill_vec, event_vec):
    model.eval()
    return model(skill_vec, event_vec)


# ---------------------------------------------------------------------
# Minimal smoke test
# ---------------------------------------------------------------------


if __name__ == "__main__":
    model = NeuralSkillWorldModel()

    skill = torch.randn(2, 5)
    event = torch.randn(2, 6)

    next_skill = model(skill, event)

    print("Predicted next skill shape:", next_skill.shape)
