from llm.rag.quality.explanation_score import score_explanation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POSITIVE_DIR = ROOT / "tests" / "golden" / "positive"


def test_positive_quality_scores():
    for file in POSITIVE_DIR.glob("*.txt"):
        text = file.read_text(encoding="utf-8")
        score = score_explanation(
            text=text,
            engine_signal=SAMPLE_ENGINE_SIGNAL,
        )
        assert score >= 7, f"{file.name} scored {score}"
