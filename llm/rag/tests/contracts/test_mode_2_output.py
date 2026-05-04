FORBIDDEN_PHRASES = [
    "best move",
    "stockfish",
    "engine",
    "depth",
    "calculate",
    "variation",
    "line",
]

REQUIRED_ON_MISSING = [
    "not enough information",
    "missing",
]

REQUIRED_ON_MATE = [
    "inevitable",
    "cannot be avoided",
]


def validate_output(text: str, *, case_type: str):
    lower = text.lower()

    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in lower, f"Forbidden phrase: {phrase}"

    if case_type == "missing_data":
        assert any(p in lower for p in REQUIRED_ON_MISSING), "Missing-data explanation not explicit"

    if case_type == "forced_mate":
        assert any(p in lower for p in REQUIRED_ON_MATE), "Mate inevitability not emphasized"
