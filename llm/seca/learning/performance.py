from dataclasses import dataclass


@dataclass
class GamePerformance:
    score: float  # 0–1 overall performance
    accuracy: float  # move quality proxy
    blunder_rate: float


def evaluate_game(moves: list[dict], result: str) -> GamePerformance:
    """
    moves: [{eval_before, eval_after, blunder_flag}, ...]
    result: "win" | "loss" | "draw"
    """

    if not moves:
        return GamePerformance(0.5, 0.5, 0.0)

    blunders = sum(1 for m in moves if m.get("blunder_flag"))
    blunder_rate = blunders / len(moves)

    accuracy = 1.0 - blunder_rate

    # result bonus
    result_bonus = {
        "win": 0.15,
        "draw": 0.0,
        "loss": -0.15,
    }.get(result, 0.0)

    score = min(max(accuracy + result_bonus, 0.0), 1.0)

    return GamePerformance(score, accuracy, blunder_rate)
