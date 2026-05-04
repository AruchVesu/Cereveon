from llm.rag.llm.base import BaseLLM


class FakeLLM(BaseLLM):
    def __init__(self, mode: str = "compliant"):
        """
        mode:
          - compliant
          - forbidden_phrase
          - missing_data_violation
          - mate_softening
        """
        self.mode = mode

    def generate(self, prompt: str) -> str:
        if self.mode == "compliant":
            return "The evaluation indicates a decisive outcome, and the position leaves little room for recovery. With accurate play, the result is inevitable over the next phase of the game."

        if self.mode == "forbidden_phrase":
            return "Stockfish shows the best move here leads to mate in 3."

        if self.mode == "missing_data_violation":
            return "White is better because of development."

        if self.mode == "mate_softening":
            return "Black still has some chances despite the position."

        raise ValueError(f"Unknown fake LLM mode: {self.mode}")
