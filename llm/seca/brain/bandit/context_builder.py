import numpy as np


def build_context_vector(
    rating_before: float,
    confidence_before: float,
    accuracy: float,
    weaknesses: dict,
) -> np.ndarray:
    """
    Build a fixed-size context vector for global bandit logging.
    """

    return np.array(
        [
            rating_before / 3000.0,
            confidence_before,
            accuracy,
            float(weaknesses.get("tactics", 0.0)),
            float(weaknesses.get("time_management", 0.0)),
            float(weaknesses.get("endgame", 0.0)),
        ],
        dtype=float,
    )
