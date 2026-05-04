from llm.seca.player.player_model import PlayerModel
from .training_tasks import TASK_LIBRARY
from .curriculum_types import TrainingTask


def select_tasks(skill: str, player: PlayerModel) -> list[TrainingTask]:
    """
    Difficulty adapts to player rating.
    """

    tasks = TASK_LIBRARY.get(skill, [])

    if not tasks:
        return []

    target_difficulty = max(1, min(10, player.rating // 300))

    return [t for t in tasks if abs(t.difficulty - target_difficulty) <= 2] or tasks[:1]
