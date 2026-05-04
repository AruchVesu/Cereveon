from __future__ import annotations
from typing import Dict, Any, List


class SkillUpdatePipeline:
    """
    Updates player skill state from stored SECA events.

    Inputs:
        - EventStorage (persistent DB)

    Responsibilities:
        - read finished game outcomes
        - compute skill deltas
        - write updated skill state back as events
    """

    def __init__(self, event_storage):
        self.events = event_storage

    # ------------------------------------------------------------------

    def run_update_cycle(self):
        """
        Main production entrypoint.
        Reads new game outcomes and emits skill updates.
        """

        outcomes = self.events.fetch_events("game_outcome")

        if not outcomes:
            return

        for outcome in outcomes:
            player_id = outcome["player_id"]
            result = outcome["result"]

            delta = self._compute_skill_delta(result)

            self.events.log_event(
                "skill_update",
                {
                    "player_id": player_id,
                    "delta": delta,
                },
            )

    # ------------------------------------------------------------------

    def _compute_skill_delta(self, result: str) -> float:
        """
        Extremely simple placeholder.
        Real system will use world model + causal engine.
        """

        if result == "win":
            return 5.0
        if result == "loss":
            return -5.0
        return 0.0
