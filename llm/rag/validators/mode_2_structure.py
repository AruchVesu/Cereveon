"""Mode-2 structural filter — advisory / move-recommendation prose.

Rules sourced from ``llm.rag.validators._rules.MOVE_ADVISORY_PATTERNS``
(single source of truth).  ``FORBIDDEN_SECTIONS`` is re-exported under
its historical public name so the callsites and tests that import it
directly continue to work.
"""

from __future__ import annotations

import re

from llm.rag.validators._rules import MOVE_ADVISORY_PATTERNS

FORBIDDEN_SECTIONS: list[str] = list(MOVE_ADVISORY_PATTERNS)


def validate_mode_2_structure(text: str) -> None:
    lowered = text.lower()
    for pattern in FORBIDDEN_SECTIONS:
        if re.search(pattern, lowered):
            raise AssertionError(f"Mode-2 structural violation: forbidden section `{pattern}`")
