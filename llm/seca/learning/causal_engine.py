class CausalEngine:
    """
    Learns which skills actually improve results.
    """

    def __init__(self, graph):
        self.graph = graph
        self.history = []

    def observe_game(self, accuracy: float, weaknesses: dict, result: float):
        """
        result: 1 win, 0 loss, 0.5 draw
        """

        self.history.append((accuracy, weaknesses, result))

        # simple causal heuristic
        for w, severity in weaknesses.items():
            improvement = (result - 0.5) * severity * 0.1
            self.graph.update_skill(w, -improvement)

        # accuracy influences win rate
        self.graph.update_skill("accuracy", (accuracy - 0.7) * 0.2)
