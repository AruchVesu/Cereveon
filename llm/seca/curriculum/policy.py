import json
from statistics import mean


class CurriculumPolicy:
    """
    Chooses WHAT to train next.
    """

    def choose_topic(self, skill_vector: dict) -> str:
        if not skill_vector:
            return "opening_principles"

        # weakest skill = lowest value
        return min(skill_vector, key=skill_vector.get)

    # ---------------------------------

    def choose_difficulty(self, rating: float, confidence: float) -> str:
        """
        Adaptive difficulty:
        - high confidence → harder
        - low confidence → easier
        """

        base = rating / 400  # ~0..6

        if confidence < 0.3:
            return "easy"

        if confidence > 0.7 and base > 3:
            return "hard"

        return "medium"

    # ---------------------------------

    def choose_exercise_type(self, topic: str) -> str:

        mapping = {
            "tactics": "puzzle",
            "endgame": "endgame_drill",
            "opening": "opening_line",
            "time_management": "blitz_simulation",
        }

        return mapping.get(topic, "mixed_training")

    # ---------------------------------

    def choose_session_length(self, confidence: float) -> int:
        """
        Minutes.
        """

        if confidence < 0.3:
            return 10

        if confidence > 0.7:
            return 30

        return 20
