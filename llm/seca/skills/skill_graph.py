from collections import defaultdict


class SkillGraph:
    """
    Directed causal skill graph for a single player.
    """

    def __init__(self):
        self.values = defaultdict(float)
        self.confidence = defaultdict(lambda: 0.5)
        self.edges = defaultdict(dict)  # A -> {B: weight}

    # -------------------------
    # update node value
    # -------------------------
    def update_skill(self, name: str, delta: float):
        self.values[name] += delta
        self.confidence[name] = min(1.0, self.confidence[name] + 0.02)

    # -------------------------
    # causal relation
    # -------------------------
    def set_edge(self, a: str, b: str, weight: float):
        self.edges[a][b] = weight

    # -------------------------
    # propagate improvement
    # -------------------------
    def propagate(self, source: str, delta: float):
        for target, w in self.edges[source].items():
            self.values[target] += delta * w
