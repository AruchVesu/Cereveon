class AdaptiveCoach:
    def __init__(self, predictor, planner):
        self.predictor = predictor
        self.planner = planner

    def next_training_step(self, player_state):
        seq, _ = self.planner(player_state)
        return seq[0]
