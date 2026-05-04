import numpy as np


def build_player_context(player, last_event) -> np.ndarray:
    """
    Convert player DB state -> feature vector.
    """

    return np.array(
        [
            player.rating,
            player.confidence,
            last_event.accuracy if last_event else 0.5,
            last_event.weaknesses.get("tactics", 0.0) if last_event else 0.0,
            last_event.weaknesses.get("time_management", 0.0) if last_event else 0.0,
            player.games_last_week,
        ],
        dtype=float,
    )
