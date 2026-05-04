import os
import pytest

from llm.rag.llm.ollama import OllamaLLM
from llm.rag.llm.run_mode_2 import run_mode_2
from llm.rag.prompts.mode_2.render import render_mode_2_prompt
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = ROOT / "tests" / "golden" / "cases"

MODEL_NAME = "qwen2.5:7b-instruct-q2_K"
TEMPERATURE = 0.2
REPEATS = 3  # repeat to catch stochastic violations

if os.getenv("RUN_OLLAMA_TESTS") != "1":
    pytest.skip(
        "Ollama regression tests are disabled by default. Set RUN_OLLAMA_TESTS=1 to enable.",
        allow_module_level=True,
    )


def load_case(case_path: Path):
    with open(case_path, "r", encoding="utf-8") as f:
        return json.load(f)


def case_type_from_path(path: Path) -> str:
    return path.parent.name


def test_llm_regression_contract():
    llm = OllamaLLM(
        model=MODEL_NAME,
        temperature=TEMPERATURE,
    )

    for case_path in CASES_DIR.rglob("case_001.json"):
        case = load_case(case_path)
        case_type = case_type_from_path(case_path)

        esv = extract_engine_signal(case.get("stockfish_json", {}))
        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

        prompt = render_mode_2_prompt(
            system_prompt=(ROOT / "rag/prompts/mode_2/system_v1.txt").read_text(encoding="utf-8"),
            engine_signal=esv,
            rag_docs=rag_docs,
            fen=case["fen"],
            user_query=case.get("user_query", ""),
        )

        for _ in range(REPEATS):
            run_mode_2(
                llm=llm,
                prompt=prompt,
                case_type=case_type,
            )
