from llm.seca.adaptation.skill_profile import build_skill_profile
from llm.seca.adaptation.teaching_policy import choose_explanation_style
from llm.seca.adaptation.opponent_policy import choose_opponent_parameters


def compute_adaptation(rating: float, confidence: float) -> dict:
    """Central adaptive brain of SECA — a heuristic, not a learned policy.

    Maps an authenticated player's (rating, confidence) to per-request
    opponent + teaching parameters. The formula is fixed; nothing here is
    online-learned (see `docs/SECA.md` and CLAUDE.md rule #3).

    Spec (also documented in README.md > Adaptation Layer):

        r = clamp((rating − 400) / 2000, 0, 1)
        explanation_depth   = r
        concept_complexity  = r ** 1.2
        opponent_strength   = r
        opponent_human_error = 1 − r

        target_elo       = int(600 + opponent_strength * 1800)   # ∈ [600, 2400]
        human_error_rate = opponent_human_error * 0.25           # ∈ [0, 0.25]
        teaching_style   = simple        if explanation_depth < 0.3
                         | intermediate  if explanation_depth < 0.7
                         | advanced      otherwise

    `confidence` is part of the signature but is NOT currently consumed by
    the formula — it is a forward-compatible hook for a future
    variance-aware adaptation. Removing the parameter would break callers
    (`server.py`, `seca/analytics/router.py`); changing the formula to
    consume it is an explicit policy change that must update both this
    docstring AND the pinned-ELO range test in
    `llm/tests/test_adaptive_engine_wiring.py`.
    """

    profile = build_skill_profile(rating, confidence)

    teaching = choose_explanation_style(profile)
    opponent = choose_opponent_parameters(profile)

    return {
        "profile": profile,
        "teaching": teaching,
        "opponent": opponent,
    }
