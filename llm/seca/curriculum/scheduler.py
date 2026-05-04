from typing import List

from llm.seca.curriculum.types import Weakness, TrainingTask
from llm.seca.curriculum.priority_model import compute_priority
from llm.seca.curriculum.task_selector import choose_task


class CurriculumScheduler:
    """
    Central decision engine of SECA training.
    """

    def __init__(self):
        pass

    def next_task(
        self,
        weaknesses: List[Weakness],
        rating: float,
    ) -> TrainingTask:
        """
        Choose optimal next training activity.
        """

        if not weaknesses:
            # fallback: balanced training game
            return TrainingTask(
                topic="general_play",
                difficulty=0.5,
                format="game",
                expected_gain=0.3,
            )

        ranked = compute_priority(weaknesses)

        top = ranked[0]

        return choose_task(top, rating)
