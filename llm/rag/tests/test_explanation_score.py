from llm.rag.quality.explanation_score import score_explanation


def test_score_high_when_good_explanation():
    text = (
        "The evaluation indicates a decisive advantage for White because White has more development.\n"
        "This is due to superior piece activity and a hanging pawn."
    )
    engine_signal = {
        "evaluation": {"band": "decisive"},
        "last_move_quality": "mistake",
        "tactical_flags": ["hanging_piece"],
    }

    sc = score_explanation(text=text, engine_signal=engine_signal)
    assert sc >= 7, f"Expected high score, got {sc}"


def test_score_low_when_missing_causal_and_flags_and_advisory():
    text = "White is better. You should play aggressively."
    engine_signal = {
        "evaluation": {"band": "advantage"},
        "last_move_quality": "mistake",
        "tactical_flags": ["hanging_piece"],
    }

    sc = score_explanation(text=text, engine_signal=engine_signal)
    assert sc < 7, f"Expected low score, got {sc}"


def test_score_penalizes_forbidden_soft_triggers():
    text = "White is better; you should play Qh5."
    engine_signal = {"evaluation": {"band": "advantage"}}

    sc = score_explanation(text=text, engine_signal=engine_signal)
    # Should be noticeably lower than an identical text without 'should'
    sc_no_should = score_explanation(text="White is better.", engine_signal=engine_signal)

    assert sc < sc_no_should, "Score did not penalize advisory wording"
