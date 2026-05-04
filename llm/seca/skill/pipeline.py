from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any

# ---------------------------------------------------------------------
# Skill state
# ---------------------------------------------------------------------


@dataclass
class SkillState:
    """
    Represents player skill vector in SECA.

    Current version:
    - simple numeric dictionary
    - later → neural latent vector
    """

    values: Dict[str, float]


# ---------------------------------------------------------------------
# Skill pipeline
# ---------------------------------------------------------------------


class SkillPipeline:
    """
    Central skill update pipeline.

    Responsibilities:
    - receive game/explanation outcomes
    - update player skill representation
    - provide current skill snapshot
    """

    def __init__(self):
        # in-memory fallback storage
        self._skills: Dict[str, SkillState] = {}

    # -----------------------------------------------------------------

    def get(self, player_id: str) -> SkillState:
        """
        Return current player skill.
        Creates default if missing.
        """
        if player_id not in self._skills:
            self._skills[player_id] = SkillState(values={})
        return self._skills[player_id]

    # -----------------------------------------------------------------

    def update(self, player_id: str, signal: Dict[str, Any]) -> SkillState:
        """
        Update skill from learning signal.

        Current logic:
        - very simple additive update
        - placeholder for neural dynamics later
        """
        skill = self.get(player_id)

        for k, v in signal.items():
            skill.values[k] = skill.values.get(k, 0.0) + float(v)

        return skill

    # -----------------------------------------------------------------

    def snapshot(self, player_id: str) -> Dict[str, float]:
        """
        Return raw skill dictionary.
        """
        return dict(self.get(player_id).values)
