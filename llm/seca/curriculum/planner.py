import random
from .actions import ACTION_LIBRARY
from .reward import reward


class CurriculumPlanner:
    """
    Model-based rollout planner.
    """

    def __init__(self, world_model, horizon: int = 5, simulations: int = 50):
        self.world_model = world_model
        self.horizon = horizon
        self.simulations = simulations

    # ---------------------------------------------------------

    def rollout(self, start_skill, first_action):
        """
        Simulate one trajectory.
        """
        skill = start_skill[:]
        total_reward = 0.0

        # apply first action
        next_skill = self.world_model.predict_next(skill, first_action.vector)
        total_reward += reward(skill, next_skill)
        skill = next_skill

        # future random actions
        for _ in range(self.horizon - 1):
            action = random.choice(ACTION_LIBRARY)
            next_skill = self.world_model.predict_next(skill, action.vector)
            total_reward += reward(skill, next_skill)
            skill = next_skill

        return total_reward

    # ---------------------------------------------------------

    def evaluate_action(self, skill, action):
        """
        Monte-Carlo estimate of action value.
        """
        total = 0.0

        for _ in range(self.simulations):
            total += self.rollout(skill, action)

        return total / self.simulations

    # ---------------------------------------------------------

    def best_action(self, skill):
        """
        Choose action with highest expected improvement.
        """
        scores = []

        for action in ACTION_LIBRARY:
            value = self.evaluate_action(skill, action)
            scores.append((value, action))

        scores.sort(key=lambda x: x[0], reverse=True)
        return scores[0][1], scores
