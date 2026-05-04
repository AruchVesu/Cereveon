import math


def next_interval(success_rate: float, previous_interval: float) -> float:
    """
    SM-2 inspired spacing.
    """

    if success_rate < 0.6:
        return 1.0

    growth = 1.8 + success_rate
    return max(1.0, previous_interval * growth)


def urgency(days_since_last: float, interval: float) -> float:
    """
    How urgently we must revisit a topic.
    """

    if interval == 0:
        return 1.0

    return min(1.0, days_since_last / interval)
