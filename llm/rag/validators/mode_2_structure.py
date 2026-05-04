import re

FORBIDDEN_SECTIONS = [
    r"\brecommended move\b",
    r"\bexample move\b",
    r"\bplan\b",
    r"\bwhite can\b",
    r"\bblack can\b",
    r"\bif it\b",
    r"\bconsider\b",
]


def validate_mode_2_structure(text: str) -> None:
    lowered = text.lower()
    for pattern in FORBIDDEN_SECTIONS:
        if re.search(pattern, lowered):
            raise AssertionError(f"Mode-2 structural violation: forbidden section `{pattern}`")
