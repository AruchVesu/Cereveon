from .planner import CurriculumPlanner


class CurriculumPolicyOptimizer:
    """
    High-level curriculum decision engine.
    """

    def __init__(self, world_model):
        self.planner = CurriculumPlanner(world_model)

    # ---------------------------------------------------------

    def recommend_next(self, skill_vector: list[float]):
        best, scores = self.planner.best_action(skill_vector)

        return {
            "action_name": best.name,
            "action_vector": best.vector,
            "ranking": [{"name": a.name, "score": float(v)} for v, a in scores],
        }
