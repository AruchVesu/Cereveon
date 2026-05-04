from collections import defaultdict


class WeaknessVectorBuilder:
    """
    Aggregates classified mistakes into skill signals.
    """

    def __init__(self):
        self.counts = defaultdict(int)
        self.total_moves = 0

    # ------------------------------------------------

    def record(self, phase: str, mistake_type: str):
        self.total_moves += 1

        if mistake_type in ("mistake", "blunder"):
            self.counts[phase] += 1

    # ------------------------------------------------

    def build(self) -> dict:
        if self.total_moves == 0:
            return {}

        return {phase: count / self.total_moves for phase, count in self.counts.items()}
