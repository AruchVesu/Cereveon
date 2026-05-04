from __future__ import annotations
from typing import Any
import os
import torch

from .model import SkillDynamicsModel


class WorldModelInference:
    """
    Runtime wrapper around trained Neural Skill World Model.

    Responsibilities:
        - load trained weights
        - provide forward prediction API
        - stay lightweight for server boot
    """

    # ------------------------------------------------------------------

    def __init__(self, model_path: str | None = None, device: str | None = None):
        """
        Args:
            model_path: path to .pt weights file
            device: "cpu" | "cuda" | None (auto)
        """

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        # Build model architecture
        self.model = SkillDynamicsModel()
        self.model.to(self.device)
        self.model.eval()

        # Load weights if provided
        if model_path and os.path.exists(model_path):
            state = torch.load(model_path, map_location=self.device)
            self.model.load_state_dict(state)
            print(f">>> WorldModel loaded: {model_path}")
        else:
            print(">>> WorldModel running with RANDOM weights (dev mode)")

    # ------------------------------------------------------------------

    @torch.no_grad()
    def predict_next(self, skill_state):
        """
        Predict next skill vector.

        Args:
            skill_state: numpy array or tensor [D]

        Returns:
            numpy array [D]
        """

        if not isinstance(skill_state, torch.Tensor):
            skill_state = torch.tensor(skill_state, dtype=torch.float32)

        skill_state = skill_state.to(self.device).unsqueeze(0)

        next_state = self.model(skill_state)

        return next_state.squeeze(0).cpu().numpy()
