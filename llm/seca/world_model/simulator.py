class CounterfactualSimulator:
    """
    Simulates future outcomes under different coaching choices.
    """

    def __init__(self, encoder, transition, outcome):
        self.encoder = encoder
        self.transition = transition
        self.outcome = outcome

    def evaluate_intervention(self, intervention_idx: int) -> float:
        """
        Predict win probability after training one skill.
        """

        state = self.encoder.encode()
        next_state = self.transition.predict_next(state, intervention_idx)
        win_prob = self.outcome.predict_win_prob(next_state)

        return float(win_prob)
