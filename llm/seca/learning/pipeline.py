from llm.seca.learning.performance import evaluate_game
from llm.seca.learning.skill_update import SkillState, update_skill
from llm.seca.learning.credit_assignment import assign_explanation_credit
from llm.seca.storage.repo import update_learning_score


def run_skill_update(
    skill: SkillState,
    moves: list[dict],
    result: str,
    explanation_logs: list[dict],
) -> SkillState:
    """
    Main SECA learning step after a finished game.
    """

    # -------------------------------------------------
    # 1. Analyze performance
    # -------------------------------------------------
    perf = evaluate_game(moves, result)

    # -------------------------------------------------
    # 2. Update player skill
    # -------------------------------------------------
    new_skill = update_skill(skill, perf.score)

    # -------------------------------------------------
    # 3. Credit assignment to explanations
    # -------------------------------------------------
    for exp in explanation_logs:
        credit = assign_explanation_credit(
            explanation_confidence=exp["confidence"],
            game_performance=perf.score,
        )

        update_learning_score(exp["id"], credit)

    return new_skill
