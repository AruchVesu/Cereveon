from llm.seca.player.player_model import PlayerModel
from .outcome_model import ExplanationOutcome


def update_rating(player: PlayerModel, outcome: ExplanationOutcome) -> None:
    """
    Adaptive training rating update.
    """

    K = 8 * player.learning_speed  # dynamic learning factor

    if outcome.outcome == "improved":
        delta = +K
        player.confidence = min(1.0, player.confidence + 0.03)

    elif outcome.outcome == "worse":
        delta = -K
        player.confidence *= 0.95
        player.current_tilt += 0.05

    else:  # neutral
        delta = 0
        player.confidence *= 0.995

    player.rating = max(100, int(player.rating + delta))
