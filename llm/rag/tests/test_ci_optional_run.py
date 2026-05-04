import os
import json
from pathlib import Path
import pytest

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.prompts.render_mode_2 import render_mode_2_prompt
from llm.rag.llm.ollama import OllamaLLM
from llm.rag.llm.run_mode_2 import run_mode_2

ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = ROOT / "tests" / "golden" / "cases"


@pytest.mark.skipif(
    os.getenv("RUN_REPR_CI") != "1", reason="Optional CI LLM test; set RUN_REPR_CI=1 to enable"
)
def test_representative_case_ci():
    """Run a single representative case against the real LLM.

    This test is skipped by default and intended to be enabled in CI when
    an LLM model is available (set `RUN_REPR_CI=1` and ensure `LLM_MODEL` is set).
    """
    model_name = os.getenv("LLM_MODEL")
    if not model_name:
        pytest.skip("No LLM_MODEL set; skipping real-LLM test")

    # Pick a single case file (case_001.json) to keep this quick
    case_path = next(CASES_DIR.rglob("case_001.json"), None)
    assert case_path is not None, "Representative case file not found"

    with open(case_path, "r", encoding="utf-8") as f:
        case = json.load(f)

    case_type = case_path.parent.name

    esv = extract_engine_signal(case.get("stockfish_json", {}))
    rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

    prompt = render_mode_2_prompt(
        engine_signal=esv,
        rag_context=rag_docs,
        fen=case["fen"],
        user_query=case.get("user_query", ""),
    )

    llm = OllamaLLM(model=model_name, temperature=float(os.getenv("LLM_TEMPERATURE", 0.2)))

    # Run once; test will fail if validators raise an AssertionError
    run_mode_2(llm=llm, prompt=prompt, case_type=case_type)
