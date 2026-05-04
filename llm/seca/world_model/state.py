import numpy as np


class PlayerStateEncoder:
    """
    Converts SkillGraph into numeric state vector.
    """

    def __init__(self, graph):
        self.graph = graph

    def encode(self) -> np.ndarray:
        skills = sorted(self.graph.values.keys())
        vec = []

        for s in skills:
            vec.append(self.graph.values[s])
            vec.append(self.graph.confidence[s])

        return np.array(vec, dtype=np.float32)
