from llm.rag.llm.config import MAX_MODE_2_RETRIES
from llm.rag.validators.mode_2_semantic import (
    validate_mode_2_semantic,
    Mode2Violation,
)
from llm.rag.llm.retry_prompt import build_retry_prompt

#: Retry budget — sourced from ``llm.rag.llm.config.MAX_MODE_2_RETRIES``
#: so the four LLM-bearing pipelines stay in lock-step on the value.
#: PR 11 (2026-05-15) consolidated four previously-independent literal
#: constants into this shared source.
MAX_RETRIES = MAX_MODE_2_RETRIES


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
