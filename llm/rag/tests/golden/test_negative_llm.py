from pathlib import Path
from llm.rag.validators.mode_2_negative import validate_mode_2_negative

NEGATIVE_DIR = Path("rag/tests/golden/negative")


def test_negative_golden_cases():
    for file in NEGATIVE_DIR.glob("*.txt"):
        text = file.read_text(encoding="utf-8")
        try:
            validate_mode_2_negative(text)
        except AssertionError:
            # EXPECTED: negative cases MUST fail validation
            continue
        else:
            raise AssertionError(f"Negative golden test did NOT fail as expected: {file.name}")
