"""Mode-2 lexical filter — regex-based forbidden-pattern rejection.

Rules sourced from ``llm.rag.validators._rules`` (single source of
truth).  ``FORBIDDEN_PATTERNS`` is the concatenated bundle consumed by
this validator; order is preserved from the pre-consolidation single
list so the violations corpus + pattern-coverage tests' literal IDs
remain stable across the 2026-05-14 ``_rules`` refactor.
"""

from __future__ import annotations

import re

from llm.rag.validators._rules import (
    ENGINE_LEXICAL_PATTERNS,
    MATE_CLAIM_PATTERNS,
    MOVE_ALGEBRAIC_PATTERNS,
    SPECULATIVE_PATTERNS,
)

# Order preserved from the pre-2026-05-14 single list:
#   speculative → algebraic → engine-lexical → mate-claim
FORBIDDEN_PATTERNS: list[str] = [
    *SPECULATIVE_PATTERNS,
    *MOVE_ALGEBRAIC_PATTERNS,
    *ENGINE_LEXICAL_PATTERNS,
    *MATE_CLAIM_PATTERNS,
]


def validate_mode_2_negative(text: str) -> None:
    """
    Raise AssertionError for invalid MODE-2 output.

    Note: the empty-input check below uses an explicit ``raise`` rather
    than ``assert``.  ``assert`` is stripped under ``python -O`` (and by
    some packaging tools that pass ``-O`` to bytecode-compile steps),
    which would silently skip the empty-output gate in production.  The
    explicit raise preserves the same exception type so existing
    callers that catch ``AssertionError`` keep working.
    """
    if not text.strip():
        raise AssertionError("Empty output is invalid")

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            raise AssertionError(f"Forbidden MODE-2 pattern detected: pattern `{pattern}`")
