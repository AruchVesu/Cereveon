"""
NON-BLOCKING, LOCAL-ONLY QUALITY HEURISTICS

These tests are NOT about correctness.
They are about explanation usefulness.

Do NOT add these to CI.
"""

from llm.rag.llm.fake import FakeLLM
from llm.rag.llm.run_mode_2 import run_mode_2

PROMPT = "Dummy prompt"

# Neutral ESV — these heuristics are NOT contract tests.  They check length /
# sentence structure of compliant FakeLLM output.  Keeping ESV neutral here
# avoids coupling this non-CI heuristic test to the ESV-conditioned semantic
# surface, which is exercised by test_run_mode_2_cascades.py.
_NEUTRAL_ESV = {
    "evaluation": {"type": "cp", "value": 0},
    "tactical_flags": ["any"],
}


def quality_heuristic(text: str):
    # Minimum length
    assert len(text.split()) >= 15, "Explanation too short"

    # Sentence structure
    assert "." in text, "No sentence structure detected"

    # Avoid extreme brevity
    assert len(text) >= 80, "Explanation lacks substance"


def test_quality_compliant_example():
    llm = FakeLLM(mode="compliant")

    response = run_mode_2(
        llm=llm,
        prompt=PROMPT,
        case_type="forced_mate",
        engine_signal=_NEUTRAL_ESV,
    )

    quality_heuristic(response)


if __name__ == "__main__":
    test_quality_compliant_example()
