def classify_delta(delta_cp: float) -> str:
    """
    Standard chess thresholds.
    """

    drop = abs(delta_cp)

    if drop < 50:
        return "ok"

    if drop < 150:
        return "inaccuracy"

    if drop < 300:
        return "mistake"

    return "blunder"
