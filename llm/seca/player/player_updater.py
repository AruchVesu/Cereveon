# seca/player/player_updater.py
from .player_model import PlayerModel


def update_from_engine_signal(player: PlayerModel, esv: dict) -> None:
    """
    Adjust player model based on position evaluation.
    """

    evaluation = esv.get("evaluation", {})
    band = evaluation.get("band")
    last_move_quality = esv.get("last_move_quality")

    # --- mistake tracking ---
    if last_move_quality in ("blunder", "mistake"):
        player.mistakes_recent += 1
        player.current_tilt += 0.1 * player.tilt_sensitivity
    else:
        player.current_tilt *= 0.9

    # --- skill learning from tactical flags ---
    if "forced_mate" in esv.get("tactical_flags", []):
        player.skills["tactics"] = min(
            1.0,
            player.skills["tactics"] + 0.01 * player.learning_speed,
        )

    # --- positional learning ---
    if esv.get("position_flags"):
        player.skills["strategy"] = min(
            1.0,
            player.skills["strategy"] + 0.005 * player.learning_speed,
        )

    # --- confidence update ---
    if band == "decisive_advantage":
        player.confidence = min(1.0, player.confidence + 0.05)
    elif band == "equal":
        player.confidence *= 0.98

    # --- games counter ---
    player.games_played += 1


def recommended_explanation_depth(player: PlayerModel) -> int:
    """
    Adaptive teaching depth.
    """

    if player.current_tilt > 0.6:
        return 1  # calm, short explanations

    if player.skills["strategy"] > 0.6:
        return 3

    return player.preferred_depth
