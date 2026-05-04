from pydantic import BaseModel


class LearningEvent(BaseModel):
    activity: str
    difficulty: float
    time_spent: float
    accuracy: float
    rating_before: int
    rating_after: int


class ActivityStat(BaseModel):
    total_time: float
    total_gain: float

    @property
    def efficiency(self):
        if self.total_time == 0:
            return 0
        return self.total_gain / self.total_time


class TrainingStat:
    def __init__(self):
        self.total_time = 0
        self.total_rating_gain = 0

    @property
    def efficiency(self):
        if self.total_time == 0:
            return 0
        return self.total_rating_gain / self.total_time


def choose_activity(stats):
    return max(stats.items(), key=lambda x: x[1].efficiency)[0]


def adjust_difficulty(accuracy, decrease_difficulty, increase_difficulty):
    if accuracy < 0.6:
        decrease_difficulty()
    elif accuracy > 0.85:
        increase_difficulty()
