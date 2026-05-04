from llm.seca.adaptation.skill_profile import SkillProfile


def choose_opponent_parameters(profile: SkillProfile) -> dict:
    """
    Maps skill → adaptive opponent configuration.
    """

    # Stockfish strength scaling
    elo = 600 + profile.opponent_strength * 1800

    # Human-like error probability
    error_rate = profile.opponent_human_error * 0.25

    return {
        "target_elo": int(elo),
        "human_error_rate": error_rate,
    }
