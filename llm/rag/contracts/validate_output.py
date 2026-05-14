"""Mode-2 broad contract check.

Substring-level filter that runs against every LLM response, plus
case-type-conditioned REQUIRE checks for forced-mate and missing-data
responses.  Forbidden phrases and required phrases now come from
``llm.rag.validators._rules`` (single source of truth shared with the
other Mode-2 validators); the module-level lists below are re-exported
under their historical public names so the 29 callsites that import
them directly continue to work.
"""

from __future__ import annotations

from llm.rag.validators._rules import (
    ENGINE_LEXICAL_PHRASES as _ENGINE_LEXICAL_PHRASES,
    MATE_INEVITABILITY_PHRASES as _MATE_INEVITABILITY_PHRASES,
    MISSING_DATA_PHRASES as _MISSING_DATA_PHRASES,
)

# Public re-exports — preserved as ``list`` (the historical type) so any
# caller that mutates the list (none today, but defensively) behaves
# unchanged.  The canonical source-of-truth tuples live in _rules.py.
FORBIDDEN_PHRASES: list[str] = list(_ENGINE_LEXICAL_PHRASES)
REQUIRED_ON_MISSING: list[str] = list(_MISSING_DATA_PHRASES)
REQUIRED_ON_MATE: list[str] = list(_MATE_INEVITABILITY_PHRASES)


def validate_output(text: str, *, case_type: str) -> None:
    lower = text.lower()

    for phrase in FORBIDDEN_PHRASES:
        if phrase in lower:
            raise AssertionError(f"Forbidden phrase detected: {phrase}")

    if case_type == "missing_data":
        if not any(p in lower for p in REQUIRED_ON_MISSING):
            raise AssertionError("Missing-data response does not acknowledge missing information")

    if case_type == "forced_mate":
        if not any(p in lower for p in REQUIRED_ON_MATE):
            raise AssertionError("Forced-mate response does not emphasize inevitability")
