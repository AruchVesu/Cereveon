from __future__ import annotations
import numpy as np
from typing import Any, Dict

from llm.seca.curriculum.policy import CurriculumPolicy
from llm.seca.world_model.safe_stub import SafeWorldModel


class CoachEngine:
    """
    Core decision brain of SECA during runtime.

    Responsibilities:
    - read current player skill vector
    - simulate candidate training actions via world model
    - select best next training step using curriculum policy
    """

    def __init__(
        self,
        world_model: SafeWorldModel | None = None,
        curriculum: CurriculumPolicy | None = None,
    ):
        self.world_model = world_model or SafeWorldModel()
        self.curriculum = curriculum or CurriculumPolicy()

    # ------------------------------------------------------------------

    def choose_next_action(
        self,
        skill_vector: np.ndarray,
    ) -> Dict[str, Any]:
        """
        Decide next coaching intervention.

        Returns:
            {
                "action_id": str,
                "predicted_gain": float,
                "next_skill": np.ndarray
            }
        """

        candidates = self.curriculum.enumerate_actions(skill_vector)

        best = None
        best_gain = -1e9

        for action in candidates:
            next_skill = self.world_model.predict_next(
                skill_vector,
                action["vector"],
            )

            gain = float(np.mean(next_skill - skill_vector))

            if gain > best_gain:
                best_gain = gain
                best = {
                    "action_id": action["id"],
                    "predicted_gain": gain,
                    "next_skill": next_skill,
                }

        return best or {
            "action_id": "noop",
            "predicted_gain": 0.0,
            "next_skill": skill_vector,
        }
