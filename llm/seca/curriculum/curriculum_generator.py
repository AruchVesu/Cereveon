from llm.seca.player.player_model import PlayerModel
from .weakness_detector import detect_primary_weakness
from .lesson_selector import select_tasks
from .curriculum_types import CurriculumPlan


def generate_curriculum(player: PlayerModel) -> CurriculumPlan:
    """
    Produces next personalized training block.
    """

    focus = detect_primary_weakness(player)

    tasks = select_tasks(focus, player)

    minutes = 5 * len(tasks)  # simple estimate

    return CurriculumPlan(
        focus_skill=focus,
        tasks=tasks,
        estimated_minutes=minutes,
    )
