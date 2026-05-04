"""
NON-BLOCKING, LOCAL-ONLY QUALITY HEURISTICS

These tests are NOT about correctness.
They are about explanation usefulness.

Do NOT add these to CI.
"""

from llm.rag.llm.fake import FakeLLM
from llm.rag.llm.run_mode_2 import run_mode_2

PROMPT = "Dummy prompt"


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
    )

    quality_heuristic(response)


if __name__ == "__main__":
    test_quality_compliant_example()
