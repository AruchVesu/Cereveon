from dataclasses import dataclass
from typing import Dict, List, Optional

# ---------------------------
# Data structures
# ---------------------------


@dataclass
class GameSummary:
    rating_before: float
    rating_after: float
    confidence_before: float
    confidence_after: float
    learning_delta: float
    weaknesses: Dict[str, float]


@dataclass
class CoachAction:
    type: str  # NONE / REFLECT / DRILL / PUZZLE / PLAN_UPDATE
    weakness: Optional[str] = None
    reason: str = ""


# ---------------------------
# Controller
# ---------------------------


class PostGameCoachController:
    def __init__(self):
        pass

    # main entry
    def decide(
        self,
        game: GameSummary,
        recent_weaknesses: List[str],
    ) -> CoachAction:

        # --- Rule 1: big improvement -> reflect
        if game.learning_delta > 20:
            return CoachAction(
                type="REFLECT",
                reason="Large positive learning spike -> consolidate insight",
            )

        # --- Rule 2: big drop -> drill main weakness
        if game.learning_delta < -20 and game.weaknesses:
            main = max(game.weaknesses, key=game.weaknesses.get)
            return CoachAction(
                type="DRILL",
                weakness=main,
                reason="Large negative learning -> immediate correction",
            )

        # --- Rule 3: confidence falling -> easy puzzle
        if game.confidence_after < game.confidence_before - 0.05:
            return CoachAction(
                type="PUZZLE",
                reason="Confidence drop -> emotional stabilization",
            )

        # --- Rule 4: stable growth -> do nothing
        if abs(game.learning_delta) < 5 and game.confidence_after >= game.confidence_before:
            return CoachAction(
                type="NONE",
                reason="Stable progress -> avoid interruption",
            )

        # --- Rule 5: repeating weakness -> plan update
        if recent_weaknesses:
            last3 = recent_weaknesses[-3:]
            if len(last3) == 3 and len(set(last3)) == 1:
                return CoachAction(
                    type="PLAN_UPDATE",
                    weakness=last3[0],
                    reason="Repeated weakness across games -> structural change",
                )

        return CoachAction(type="NONE", reason="No trigger")
