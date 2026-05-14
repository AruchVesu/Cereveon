"""Mode-2 semantic filter — ESV-conditioned rejection rules.

Rules sourced from ``llm.rag.validators._rules`` (single source of
truth).  Pre-2026-05-14 this module carried two unused module-level
constants (FORBIDDEN_EQUAL / FORBIDDEN_ENGINE_SPECULATION) in regex
form while the function body used hardcoded plain-string lists — the
constants were dead code.  The refactor deletes them and points the
function at the shared substring sets.
"""

from __future__ import annotations

from llm.rag.validators._rules import (
    EQUAL_ADVANTAGE_WORDS,
    MATE_INEVITABILITY_SEMANTIC,
    SPECULATIVE_SEMANTIC,
    TACTICAL_NOUN_WORDS,
)


class Mode2Violation(Exception):
    pass


def validate_mode_2_semantic(text: str, engine_signal: dict) -> None:
    evaluation = engine_signal.get("evaluation", {})
    band = evaluation.get("band")
    eval_type = evaluation.get("type")
    tactical_flags = engine_signal.get("tactical_flags", [])

    lower = text.lower()

    # Equal neutrality — Row 8 of the Validator Coverage Matrix.
    if band == "equal":
        for word in EQUAL_ADVANTAGE_WORDS:
            if word in lower:
                raise Mode2Violation(f"Equal position described as advantage: '{word}'")

    # Mate decisiveness — Row 5 (semantic REQUIRE).
    if eval_type == "mate":
        if not any(p in lower for p in MATE_INEVITABILITY_SEMANTIC):
            raise Mode2Violation("Mate not described as forced/inevitable")

    # Engine speculation — Row 4 (semantic mirror of the lexical filter).
    for word in SPECULATIVE_SEMANTIC:
        if word in lower:
            raise Mode2Violation(f"Speculative language detected: '{word}'")

    # Invented tactics — Row 9.
    if not tactical_flags:
        for word in TACTICAL_NOUN_WORDS:
            if word in lower:
                raise Mode2Violation(f"Invented tactic without flag: '{word}'")
