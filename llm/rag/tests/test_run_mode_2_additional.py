import os
import re
import pytest

from llm.rag.llm.run_mode_2 import run_mode_2
from llm.rag.llm.fake import FakeLLM


class StubbornLLM:
    """LLM that always returns the same forbidden-heavy text, even for rewrites."""

    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.text


def test_aggressive_sanitization_applies():
    bad = "Stockfish shows the best move leading to checkmate. You should play Qh5."
    llm = StubbornLLM(bad)

    out = run_mode_2(llm=llm, prompt="PROMPT", case_type="tactical")

    lower = out.lower()
    assert "checkmate" not in lower
    assert "mate in" not in lower
    assert "stockfish" not in lower
    assert "[redacted]" in out or "decisive advantage" in lower

    # Ensure we attempted rewrites up to the retry budget
    from llm.rag.llm.config import MAX_MODE_2_RETRIES

    assert len(llm.calls) >= 1 + MAX_MODE_2_RETRIES
    # And that we didn't exceed it
    assert len(llm.calls) <= 1 + MAX_MODE_2_RETRIES


def test_retries_exhaustion_raises_for_forced_mate():
    # When case_type is forced_mate, validators require 'cannot be avoided' or 'inevitable'.
    # If LLM keeps returning texts that don't satisfy this, run_mode_2 should ultimately raise.
    bad = "Stockfish shows mate in 3; forced mate is obvious."
    llm = StubbornLLM(bad)

    from llm.rag.llm.config import MAX_MODE_2_RETRIES

    with pytest.raises(AssertionError):
        run_mode_2(llm=llm, prompt="PROMPT", case_type="forced_mate")

    # Ensure we used our retry budget
    assert len(llm.calls) >= 1 + MAX_MODE_2_RETRIES
    # And that we didn't exceed it
    assert len(llm.calls) <= 1 + MAX_MODE_2_RETRIES


def test_explain_position_confidence_low_for_low_score(tmp_path, monkeypatch):
    # Use the FakeLLM missing_data_violation output which should produce a low score
    monkeypatch.setenv("LLM_MODEL", "fake:missing_data_violation")

    # Re-import the embedded module to pick up the env var (safe in tests)
    import importlib
    import llm.rag.deploy.embedded as embedded

    importlib.reload(embedded)

    payload = {
        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/5N2/PPP2PPP/RNBQKB1R b KQkq - 2 3",
        "engine_json": {
            "evaluation": {"type": "cp", "value": -180},
            "eval_delta": -150,
            "errors": {"last_move_quality": "mistake"},
            "tactical_flags": ["hanging_piece"],
        },
    }

    res = embedded.explain_position(payload)
    assert res["confidence"] in ("low", "high")
    assert res["confidence"] == "low"
