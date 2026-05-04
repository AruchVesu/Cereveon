from rag.llm.ollama import OllamaLLM
from rag.llm.run_mode_2 import run_mode_2

PROMPT = """
ENGINE SIGNAL:
evaluation.type = mate
forced mate

Explain the position.
"""


def test_ollama_forced_mate_smoke():
    llm = OllamaLLM(
        model="qwen2.5:7b-instruct-q2_K",  # <-- change to your model name
        temperature=0.2,
    )

    response = run_mode_2(
        llm=llm,
        prompt=PROMPT,
        case_type="forced_mate",
    )

    assert isinstance(response, str)
    assert len(response) > 20
