def rating_from_skill(skill: list[float]) -> float:
    """
    Convert latent skill → Elo estimate.
    Simple linear projection for now.
    """
    if not skill:
        return 800.0
    return 800 + 400 * sum(skill) / len(skill)


def reward(skill_before: list[float], skill_after: list[float]) -> float:
    """
    Reward = Elo improvement.
    """
    return rating_from_skill(skill_after) - rating_from_skill(skill_before)
