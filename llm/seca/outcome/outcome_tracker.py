from .outcome_model import ExplanationOutcome


def evaluate_outcome(
    player_id: str,
    prev_last_move_quality: str,
    next_last_move_quality: str,
) -> ExplanationOutcome:

    mistake_before = prev_last_move_quality in ("blunder", "mistake")
    mistake_after = next_last_move_quality in ("blunder", "mistake")

    if mistake_before and not mistake_after:
        outcome = "improved"
    elif mistake_before and mistake_after:
        outcome = "worse"
    else:
        outcome = "neutral"

    return ExplanationOutcome(
        player_id=player_id,
        outcome=outcome,
        mistake_before=mistake_before,
        mistake_after=mistake_after,
    )
