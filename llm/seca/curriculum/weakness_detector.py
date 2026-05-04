from llm.seca.player.player_model import PlayerModel


def detect_primary_weakness(player: PlayerModel) -> str:
    """
    Returns weakest skill name.
    """

    skills = player.skill_vector

    # lowest numeric score
    weakest = min(skills, key=skills.get)

    # tilt override → mental training first
    if player.current_tilt > 0.6:
        return "tilt_control"

    return weakest
