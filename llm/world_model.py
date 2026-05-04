"""
SECA Neural Skill World Model — Production Inference Engine
----------------------------------------------------------
Provides runtime utilities for:
- Loading trained PyTorch world model
- Predicting next skill state after training action
- Simulating curriculum trajectories
- Estimating improvement and uncertainty

Designed to be imported by:
- Curriculum Scheduler
- Coach Engine
- RL Policy Optimizer
- Adaptive Opponent Controller
"""

from __future__ import annotations

import torch
import numpy as np
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------
# PyTorch model definition (must match training architecture)
# ---------------------------------------------------------------------


class SkillWorldModel(torch.nn.Module):
    """Simple MLP world model for skill transition prediction."""

    def __init__(self, skill_dim: int = 40, hidden: int = 128):
        super().__init__()

        self.net = torch.nn.Sequential(
            torch.nn.Linear(skill_dim + 1, hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(hidden, hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(hidden, skill_dim),
        )

    def forward(self, skill: torch.Tensor, action: torch.Tensor) -> torch.Tensor:
        """
        skill:  [B, skill_dim]
        action: [B, 1]
        """
        x = torch.cat([skill, action], dim=-1)
        return self.net(x)


# ---------------------------------------------------------------------
# Inference wrapper
# ---------------------------------------------------------------------


class WorldModel:
    """Production wrapper around trained neural skill dynamics model."""

    def __init__(
        self,
        model_path: str | Path,
        device: str | None = None,
        skill_dim: int = 40,
    ):
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        self.model = SkillWorldModel(skill_dim=skill_dim)
        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
        self.model.to(self.device)
        self.model.eval()

        self.skill_dim = skill_dim

    # -----------------------------------------------------------------
    # Core prediction
    # -----------------------------------------------------------------

    @torch.no_grad()
    def predict_next(self, skill: np.ndarray, action: float) -> np.ndarray:
        """Predict next skill vector after a single training action."""

        skill_t = torch.tensor(skill, dtype=torch.float32, device=self.device).unsqueeze(0)
        action_t = torch.tensor([[action]], dtype=torch.float32, device=self.device)

        next_skill = self.model(skill_t, action_t)
        return next_skill.squeeze(0).cpu().numpy()

    # -----------------------------------------------------------------
    # Trajectory simulation
    # -----------------------------------------------------------------

    @torch.no_grad()
    def simulate_trajectory(
        self,
        start_skill: np.ndarray,
        actions: List[float],
    ) -> np.ndarray:
        """Roll forward skill trajectory under a sequence of actions."""

        skill = np.asarray(start_skill, dtype=np.float32)
        traj = [skill]

        for a in actions:
            skill = self.predict_next(skill, a)
            traj.append(skill)

        return np.stack(traj)

    # -----------------------------------------------------------------
    # Improvement estimation
    # -----------------------------------------------------------------

    def estimate_improvement(
        self,
        start_skill: np.ndarray,
        actions: List[float],
        metric_index: int = 0,
    ) -> float:
        """Return scalar improvement in selected skill dimension."""

        traj = self.simulate_trajectory(start_skill, actions)
        return float(traj[-1, metric_index] - traj[0, metric_index])

    # -----------------------------------------------------------------
    # Uncertainty estimation (MC dropout style via noise injection)
    # -----------------------------------------------------------------

    @torch.no_grad()
    def estimate_uncertainty(
        self,
        skill: np.ndarray,
        action: float,
        samples: int = 20,
        noise_std: float = 0.01,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Returns:
        - mean prediction
        - std deviation (uncertainty proxy)
        """

        preds = []

        for _ in range(samples):
            noisy_skill = skill + np.random.randn(*skill.shape) * noise_std
            preds.append(self.predict_next(noisy_skill, action))

        preds = np.stack(preds)
        return preds.mean(axis=0), preds.std(axis=0)


# ---------------------------------------------------------------------
# Utility loader
# ---------------------------------------------------------------------


def load_world_model(path: str | Path, device: str | None = None) -> WorldModel:
    """Convenience factory."""
    return WorldModel(model_path=path, device=device)


# ---------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------


if __name__ == "__main__":
    print("Running SECA World Model self-test...")

    dummy_model_path = Path("world_model.pt")

    if not dummy_model_path.exists():
        print("No trained model found → creating random weights for smoke test")
        torch.save(SkillWorldModel().state_dict(), dummy_model_path)

    wm = load_world_model(dummy_model_path)

    skill0 = np.zeros(40, dtype=np.float32)
    actions = [0.2, 0.5, 0.1, 0.3]

    traj = wm.simulate_trajectory(skill0, actions)
    improvement = wm.estimate_improvement(skill0, actions)
    mean, std = wm.estimate_uncertainty(skill0, 0.5)

    print("Trajectory shape:", traj.shape)
    print("Improvement:", improvement)
    print("Uncertainty mean/std shapes:", mean.shape, std.shape)
