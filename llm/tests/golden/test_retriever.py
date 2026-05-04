import json
from pathlib import Path

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS

ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = ROOT / "tests" / "golden" / "cases"
EXPECTED_DIR = ROOT / "tests" / "golden" / "expected_rag"


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_case(case_path: Path):
    category = case_path.parent.name
    case_id = case_path.stem

    expected_path = EXPECTED_DIR / category / f"{case_id}.json"

    case = load_json(case_path)
    expected_ids = load_json(expected_path)

    esv = extract_engine_signal(case["stockfish_json"])
    docs = retrieve(esv, ALL_RAG_DOCUMENTS)

    retrieved_ids = [doc["id"] for doc in docs]

    return retrieved_ids, expected_ids


def test_all_golden_retriever_cases():
    failures = []

    for case_path in CASES_DIR.rglob("case_*.json"):
        retrieved, expected = run_case(case_path)

        if retrieved != expected:
            failures.append(
                {
                    "case": str(case_path),
                    "expected": expected,
                    "retrieved": retrieved,
                }
            )

    if failures:
        raise AssertionError("Golden retriever test failures:\n" + json.dumps(failures, indent=2))
