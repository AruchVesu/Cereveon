from llm.rag.llm.run_mode_2 import run_mode_2


class FakeLLM:
    def __init__(self):
        self.calls = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        # Initial generation returns a mate claim and a forbidden "Stockfish" phrase
        if "REWRITE INSTRUCTIONS" not in prompt:
            return "Stockfish shows the best move here leads to mate in 3. Checkmate appears unavoidable."
        # A rewrite (what a helpful model would return)
        return "The evaluation indicates a decisive advantage for Black."


def test_run_mode_2_quick_mate_sanitization():
    llm = FakeLLM()
    prompt = "SOME PROMPT"

    result = run_mode_2(llm=llm, prompt=prompt, case_type="tactical")

    # The initial generation should have occurred at least once
    assert len(llm.calls) >= 1

    # The returned text must not contain 'mate' nor 'checkmate' nor 'stockfish'
    lower = result.lower()
    assert "checkmate" not in lower
    assert "mate in" not in lower
    assert "stockfish" not in lower
    assert "decisive advantage" in lower or "decisive" in lower
