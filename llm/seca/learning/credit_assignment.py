def assign_explanation_credit(
    explanation_confidence: float,
    game_performance: float,
) -> float:
    """
    Returns learning score for this is explanation effectiveness.
    """

    # If performance > expected → positive credit
    delta = game_performance - explanation_confidence

    # squash to −1…1
    score = max(-1.0, min(1.0, delta * 2))

    return score
