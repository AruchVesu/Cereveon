from llm.seca.performance.confidence import GamePerformance


def build_game_performance(
    timeline: dict,
    expected_score: float,
    actual_score: float,
    missed_tactics: int = 0,
    total_tactics: int = 0,
) -> GamePerformance:
    """
    Converts raw engine timeline into GamePerformance dataclass.
    """

    return GamePerformance(
        moves=timeline["moves"],
        blunders=timeline["blunders"],
        acpl=timeline["acpl"],
        eval_series=timeline["eval_series"],
        missed_tactics=missed_tactics,
        total_tactics=total_tactics,
        expected_score=expected_score,
        actual_score=actual_score,
    )
