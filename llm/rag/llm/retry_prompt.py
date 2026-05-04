def build_retry_prompt(
    original_prompt: str,
    violation: str,
) -> str:
    """
    Adds strict corrective instruction for regeneration.
    """

    correction_block = f"""
The previous explanation violated a strict rule:

VIOLATION:
{violation}

You MUST regenerate the explanation while:
- strictly following the engine signal
- avoiding the violation above
- remaining concise and factual
- not adding speculative or invented content

Return ONLY the corrected explanation text.
"""

    return original_prompt + "\n\n" + correction_block
