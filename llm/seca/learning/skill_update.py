from dataclasses import dataclass


@dataclass
class SkillState:
    rating: float = 1200.0
    confidence: float = 0.5


def update_skill(state: SkillState, performance_score: float) -> SkillState:
    """
    Bayesian-like smooth update.
    """

    # learning rate shrinks as confidence grows
    lr = 32 * (1.0 - state.confidence)

    delta = (performance_score - 0.5) * lr

    new_rating = state.rating + delta

    # confidence increases with games played implicitly
    new_conf = min(1.0, state.confidence + 0.02)

    return SkillState(new_rating, new_conf)
