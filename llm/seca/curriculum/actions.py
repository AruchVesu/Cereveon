from dataclasses import dataclass
import random


@dataclass
class TrainingAction:
    vector: list[float]
    name: str


# ---------------------------------------------------------

ACTION_LIBRARY = [
    TrainingAction([1, 0.3, 0.5, 1, 0, 0.2], "Easy tactics"),
    TrainingAction([1, 0.6, 0.6, 1, 0, 0.1], "Hard tactics"),
    TrainingAction([2, 0.5, 0.7, 0, 1, 0.2], "Strategy study"),
    TrainingAction([3, 0.7, 1.0, 0.5, 0.5, 0.3], "Training game"),
    TrainingAction([4, 0.4, 0.4, 0, 0, 1.0], "Mistake review"),
]


def sample_random_action():
    return random.choice(ACTION_LIBRARY)
