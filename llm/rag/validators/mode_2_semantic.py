"""Mode-2 semantic filter — ESV-conditioned rejection rules.

Rules sourced from ``llm.rag.validators._rules`` (single source of
truth).  Pre-2026-05-14 this module carried two unused module-level
constants (FORBIDDEN_EQUAL / FORBIDDEN_ENGINE_SPECULATION) in regex
form while the function body used hardcoded plain-string lists — the
constants were dead code.  The refactor deletes them and points the
function at the shared substring sets.

2026-06-07: the forbidding checks switched from raw substring
(``word in lower``) to WORD-BOUNDARY matching.  Raw substring matching
was catastrophically over-broad — e.g. the tactic ``pin`` fired inside
``develoPINg`` / ``stepPINg`` / ``keePINg``, so ordinary coaching prose
on any quiet position tripped "Invented tactic without flag: 'pin'" and
fell through to the templated deterministic fallback (real-model
diagnostic).  The lexical layer (``mode_2_negative``) has always used
``\\b...\\b`` regex; this aligns the semantic layer with it.  The
MATE-inevitability check stays a substring REQUIRE (it only asks whether
an accept-word is present anywhere).
"""

from __future__ import annotations

import re

from llm.rag.validators._rules import (
    EQUAL_ADVANTAGE_WORDS,
    MATE_INEVITABILITY_SEMANTIC,
    SPECULATIVE_SEMANTIC,
    TACTICAL_NOUN_WORDS,
)


class Mode2Violation(Exception):
    pass


def _word_present(word: str, lower: str) -> bool:
    """True if ``word`` occurs as a whole word/phrase in ``lower``.

    Word-boundary match, NOT raw substring: "pin" must not match
    "developing"/"stepping", "better" must not match "betterment", etc.
    ``word`` may be a multi-word phrase ("slight advantage"); the
    boundaries wrap the whole phrase.
    """
    return re.search(rf"\b{re.escape(word)}\b", lower) is not None


def validate_mode_2_semantic(
    text: str, engine_signal: dict, *, check_mate_require: bool = True
) -> None:
    """Validate Mode-2 semantic contracts against the engine signal.

    ``check_mate_require`` exists for INCREMENTAL (streaming) validation:
    the mate-inevitability rule below is a REQUIRE (the word must be
    PRESENT somewhere in the reply), which can only be judged on the
    complete text — a partial buffer legitimately may not contain
    "inevitable" yet.  The streaming validate-before-emit path calls this
    with ``check_mate_require=False`` on each partial buffer (FORBID gates
    only) and re-runs the full check (REQUIRE included) once the stream
    ends.  All the other checks here are FORBID rules — safe to run on a
    partial buffer.
    """
    evaluation = engine_signal.get("evaluation", {})
    band = evaluation.get("band")
    eval_type = evaluation.get("type")
    tactical_flags = engine_signal.get("tactical_flags", [])

    lower = text.lower()

    # Equal neutrality — Row 8 of the Validator Coverage Matrix.
    if band == "equal":
        for word in EQUAL_ADVANTAGE_WORDS:
            if _word_present(word, lower):
                raise Mode2Violation(f"Equal position described as advantage: '{word}'")

    # Mate decisiveness — Row 5 (semantic REQUIRE).  Substring is fine for
    # a presence check (we only ask whether an accept-word appears).
    if check_mate_require and eval_type == "mate":
        if not any(p in lower for p in MATE_INEVITABILITY_SEMANTIC):
            raise Mode2Violation("Mate not described as forced/inevitable")

    # Engine speculation — Row 4 (semantic mirror of the lexical filter).
    for word in SPECULATIVE_SEMANTIC:
        if _word_present(word, lower):
            raise Mode2Violation(f"Speculative language detected: '{word}'")

    # Invented tactics — Row 9.
    if not tactical_flags:
        for word in TACTICAL_NOUN_WORDS:
            if _word_present(word, lower):
                raise Mode2Violation(f"Invented tactic without flag: '{word}'")
