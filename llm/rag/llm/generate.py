from llm.rag.validators.mode_2_semantic import (
    validate_mode_2_semantic,
    Mode2Violation,
)
from llm.rag.llm.retry_prompt import build_retry_prompt

MAX_RETRIES = 2


def generate_with_adaptive_retry(
    *,
    call_llm,
    prompt: str,
    engine_signal: dict,
) -> str:
    """
    Deterministic safe generation with adaptive retries.
    """

    current_prompt = prompt

    for _ in range(MAX_RETRIES + 1):
        text = call_llm(current_prompt)

        try:
            validate_mode_2_semantic(text, engine_signal)
            return text
        except Mode2Violation as e:
            current_prompt = build_retry_prompt(
                original_prompt=prompt,
                violation=str(e),
            )

    # Safe deterministic fallback
    return (
        "The position appears balanced according to the evaluation signal, "
        "with no confirmed tactical or decisive positional factors."
    )
