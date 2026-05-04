from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class CoachContent:
    title: str
    description: str
    payload: Dict[str, Any]


class CoachExecutor:
    """
    Converts abstract coach_action → real user content.
    """

    def execute(self, action) -> CoachContent:
        handler = getattr(self, f"_handle_{action.type.lower()}", self._handle_default)
        return handler(action)

    # ---------------- DRILL ----------------

    def _handle_drill(self, action) -> CoachContent:
        weakness = action.weakness or "general"

        return CoachContent(
            title=f"Targeted drill: {weakness.replace('_', ' ').title()}",
            description="Short focused exercise to improve your weakest skill.",
            payload={
                "duration_min": 10,
                "steps": [
                    "Solve 5 focused positions",
                    "Review mistakes",
                    "Repeat key motif",
                ],
                "weakness": weakness,
            },
        )

    # ---------------- PUZZLES ----------------

    def _handle_puzzle_set(self, action) -> CoachContent:
        weakness = action.weakness or "tactics"

        return CoachContent(
            title=f"Adaptive puzzle set ({weakness})",
            description="Training positions chosen for your current level.",
            payload={
                "puzzle_count": 5,
                "theme": weakness,
                "rating_offset": -50,
            },
        )

    # ---------------- REFLECT ----------------

    def _handle_reflect(self, action) -> CoachContent:
        return CoachContent(
            title="Post-game reflection",
            description="Think before the next game to consolidate learning.",
            payload={
                "questions": [
                    "Where was the critical moment?",
                    "What plan did I miss?",
                    "What will I try next game?",
                ]
            },
        )

    # ---------------- REST ----------------

    def _handle_rest(self, action) -> CoachContent:
        return CoachContent(
            title="Recovery break",
            description="Short rest to prevent fatigue and rating drop.",
            payload={"suggestion": "Take a 10-minute walk and return refreshed."},
        )

    # ---------------- PUZZLE ----------------

    def _handle_puzzle(self, action) -> CoachContent:
        weakness = action.weakness or "tactics"

        return CoachContent(
            title=f"Puzzle challenge ({weakness})",
            description="Solve this position to sharpen your calculation.",
            payload={
                "puzzle_count": 3,
                "theme": weakness,
                "difficulty": "adaptive",
            },
        )

    # ---------------- PLAN UPDATE ----------------

    def _handle_plan_update(self, action) -> CoachContent:
        weakness = action.weakness or "general"

        return CoachContent(
            title="Updated training plan",
            description=f"Focus area adjusted: {weakness.replace('_', ' ').title()}.",
            payload={
                "updated_focus": weakness,
                "reason": action.reason or "Based on recent game performance.",
            },
        )

    # ---------------- DEFAULT ----------------

    def _handle_default(self, action) -> CoachContent:
        return CoachContent(
            title="Keep playing",
            description="No special training needed right now.",
            payload={},
        )
