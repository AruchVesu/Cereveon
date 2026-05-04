FORBIDDEN_PHRASES = [
    "stockfish",
    "best move",
    "engine",
    "depth",
    "calculate",
    "variation",
]

REQUIRED_ON_MISSING = [
    "missing",
    "not enough information",
]

REQUIRED_ON_MATE = [
    "cannot be avoided",
    "inevitable",
    # "unavoidable" is the adjective form of "cannot be avoided" — accepting
    # it broadens the contract without weakening the safety claim.  Models
    # naturally produce phrases like "decisive and unavoidable disadvantage"
    # when prompted on a forced mate; without this entry the contract test
    # rejected semantically-correct output.
    "unavoidable",
]


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
