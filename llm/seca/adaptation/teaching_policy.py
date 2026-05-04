from llm.seca.adaptation.skill_profile import SkillProfile


def choose_explanation_style(profile: SkillProfile) -> dict:
    """
    Returns parameters for explanation planner + LLM controller.
    """

    if profile.explanation_depth < 0.3:
        return {
            "style": "simple",
            "max_concepts": 1,
            "allow_variations": False,
            "sentence_length": "short",
        }

    if profile.explanation_depth < 0.7:
        return {
            "style": "intermediate",
            "max_concepts": 2,
            "allow_variations": True,
            "sentence_length": "medium",
        }

    return {
        "style": "advanced",
        "max_concepts": 4,
        "allow_variations": True,
        "sentence_length": "long",
    }
