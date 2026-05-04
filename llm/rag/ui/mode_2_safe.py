from llm.rag.validators.mode_2_negative import validate_mode_2_negative

SAFE_FALLBACK = (
    "The position has been evaluated by the engine, "
    "but a detailed explanation is currently unavailable."
)


def safe_mode_2_output(llm_output: str) -> str:
    """
    Returns text that is ALWAYS safe for UI.
    Never raises.
    """
    try:
        # MODE-2 negative validator MUST fail
        validate_mode_2_negative(llm_output)
    except AssertionError:
        # Expected → invalid → fallback
        return SAFE_FALLBACK

    # Defensive: should never happen
    return SAFE_FALLBACK
