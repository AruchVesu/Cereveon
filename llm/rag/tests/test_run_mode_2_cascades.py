import re

import pytest

from llm.rag.llm.run_mode_2 import run_mode_2

# Neutral ESV — disables every ESV-gated semantic check so the existing
# cascades exercise the lexical/structure/output validator chain only.
# A dedicated semantic-cascade test below uses a band="equal" ESV to pin
# the new validator-parity surface.
_NEUTRAL_ESV = {
    "evaluation": {"type": "cp", "value": 0},
    "tactical_flags": ["any"],
}


class FakeLLM:
    def __init__(self, initial: str, rewritten: str):
        self.initial = initial
        self.rewritten = rewritten
        self.calls = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        # If the prompt is a rewrite request, return the rewritten safe text
        if "REWRITE INSTRUCTIONS" in prompt:
            return self.rewritten
        return self.initial


def _assert_sanitized(text: str):
    lower = text.lower()
    assert "checkmate" not in lower, f"Still contains checkmate: {text}"
    assert "mate in" not in lower, f"Still contains 'mate in': {text}"
    assert "stockfish" not in lower, f"Still contains 'stockfish': {text}"
    assert not re.search(
        r"\b(should|must|needs to|best move)\b", lower
    ), f"Still contains advisory language: {text}"
    assert not re.search(r"\b[bnrqk]?[a-h][1-8]\b", lower), f"Still contains notation: {text}"
    # structural headings/triggers should be removed
    assert not re.search(
        r"(?im)^\s*(recommended move|example move|plan)[:\s]?.*$", text
    ), f"Still contains heading: {text}"
    assert not re.search(
        r"(?i)\b(white can|black can|if it|consider)\b", lower
    ), f"Still contains structural phrasing: {text}"


def test_notation_sanitization_cascade():
    # Initial output uses algebraic notation and coordinates
    initial = "After 1. e4 e5 2. Nf3, White's knight on f3 is active and White is better."
    rewritten = (
        "The evaluation indicates a development advantage for White and greater piece activity."
    )

    llm = FakeLLM(initial=initial, rewritten=rewritten)

    out = run_mode_2(llm=llm, prompt="PROMPT", case_type="tactical", engine_signal=_NEUTRAL_ESV)

    _assert_sanitized(out)
    # At least one generation occurred and a rewrite should have been requested
    from llm.rag.llm.config import MAX_MODE_2_RETRIES

    assert len(llm.calls) >= 2
    assert any("REWRITE INSTRUCTIONS" in c for c in llm.calls)
    # Don't exceed the retry budget (initial + MAX retries)
    assert len(llm.calls) <= 1 + MAX_MODE_2_RETRIES


def test_mate_notation_advisory_cascade():
    # Initial output contains stockfish, advisory phrasing, mate claim, and notation
    initial = "Stockfish shows the best move is Qh5 leading to mate in 3. You should play Qh5."
    rewritten = "The evaluation indicates a decisive advantage for White without specifying moves."

    llm = FakeLLM(initial=initial, rewritten=rewritten)

    out = run_mode_2(llm=llm, prompt="PROMPT", case_type="tactical", engine_signal=_NEUTRAL_ESV)

    _assert_sanitized(out)
    # A rewrite should have been requested and applied
    from llm.rag.llm.config import MAX_MODE_2_RETRIES

    assert len(llm.calls) >= 2
    assert any("REWRITE INSTRUCTIONS" in c for c in llm.calls)
    # Don't exceed the retry budget
    assert len(llm.calls) <= 1 + MAX_MODE_2_RETRIES


def test_structure_advisory_notation_cascade():
    # Initial output contains a forbidden 'Plan' heading, advisory language and coordinates
    initial = "Plan: White can play Qh5 and then 0-0. You should look for this idea."
    rewritten = "The evaluation explains that White's activity and castling options increase pressure on Black's position."

    llm = FakeLLM(initial=initial, rewritten=rewritten)

    out = run_mode_2(llm=llm, prompt="PROMPT", case_type="tactical", engine_signal=_NEUTRAL_ESV)

    _assert_sanitized(out)
    # Structure rewrite should have been requested (or a rewrite was used)
    from llm.rag.llm.config import MAX_MODE_2_RETRIES

    assert len(llm.calls) >= 2
    assert any("REWRITE INSTRUCTIONS" in c for c in llm.calls)
    # Don't exceed the retry budget
    assert len(llm.calls) <= 1 + MAX_MODE_2_RETRIES


def test_semantic_violation_on_equal_band_triggers_retry():
    """Equal-band ESV must wire validate_mode_2_semantic into the retry loop.

    Closes the parity gap with _build_chat_llm / _build_hint_llm: a borderline
    output that passes negative + structure + output but says "Black is winning"
    when the engine signal reports band=="equal" used to slip through
    run_mode_2's retry loop because the semantic validator wasn't part of
    _validate_all.  This pins the fix so a future refactor can't silently
    drop semantic again without breaking a deterministic test.

    Uses "winning" (was "better" until 2026-06-07, when "better" was retired
    from EQUAL_ADVANTAGE_WORDS as too common a comparative — see
    test_semantic_strategic_vocab_unlock.py).
    """
    equal_band_esv = {
        "evaluation": {"type": "cp", "value": 0, "band": "equal"},
        "tactical_flags": ["any"],
    }
    # Initial output: passes negative/structure/output but fails semantic
    # ("winning" is in EQUAL_ADVANTAGE_WORDS, gated on band=="equal").
    initial = "The position is roughly balanced and Black is winning in this phase."
    rewritten = "The position is roughly balanced with no decisive imbalance for either side."

    llm = FakeLLM(initial=initial, rewritten=rewritten)

    out = run_mode_2(
        llm=llm,
        prompt="PROMPT",
        case_type="general",
        engine_signal=equal_band_esv,
    )

    # Rewritten output is what the test consumed — the initial was rejected
    # by the semantic gate inside the retry loop.
    assert "winning" not in out.lower()
    # A rewrite must have been requested as a result of the semantic failure.
    assert any("REWRITE INSTRUCTIONS" in c for c in llm.calls)
    from llm.rag.llm.config import MAX_MODE_2_RETRIES

    assert len(llm.calls) <= 1 + MAX_MODE_2_RETRIES


def test_semantic_violation_falls_through_when_unfixable():
    """Stubborn LLM that keeps emitting an equal-band advantage claim must
    surface an AssertionError after exhausting the retry budget.

    Pairs with the cascade test above: confirms the retry loop is what gates
    the failure, not a transient code path that skips semantic.
    """
    equal_band_esv = {
        "evaluation": {"type": "cp", "value": 0, "band": "equal"},
        "tactical_flags": ["any"],
    }
    # Always returns the same advantage-laden text — even the rewrite path.
    bad = "The position is balanced and Black is winning."

    class StubbornLLM:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def generate(self, prompt: str) -> str:
            self.calls.append(prompt)
            return bad

    llm = StubbornLLM()

    with pytest.raises(AssertionError):
        run_mode_2(
            llm=llm,
            prompt="PROMPT",
            case_type="general",
            engine_signal=equal_band_esv,
        )

    from llm.rag.llm.config import MAX_MODE_2_RETRIES

    assert len(llm.calls) >= 1 + MAX_MODE_2_RETRIES
    assert len(llm.calls) <= 1 + MAX_MODE_2_RETRIES
