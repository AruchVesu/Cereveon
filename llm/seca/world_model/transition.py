import numpy as np


class SkillTransitionModel:
    """
    Predicts next state after training focus.
    """

    def __init__(self, dim: int):
        # linear dev model (replace later with NN)
        self.W = np.eye(dim)

    def predict_next(self, state: np.ndarray, intervention_idx: int):
        """
        Apply improvement to one skill dimension.
        """
        delta = np.zeros_like(state)
        delta[intervention_idx] = 0.1

        return state + self.W @ delta
