import json
from pathlib import Path

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.prompts.mode_2.render_v1 import render_mode_2_prompt

ROOT = Path(__file__).resolve().parents[3]

SYSTEM_PROMPT = (
    (ROOT / "rag" / "prompts" / "mode_2" / "system_v1.txt").read_text(encoding="utf-8").strip()
)


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_all_golden_prompt_snapshots():
    cases_dir = ROOT / "tests" / "golden" / "cases"
    prompts_dir = ROOT / "tests" / "golden" / "prompts"

    failures = []

    for case_path in cases_dir.rglob("case_*.json"):
        category = case_path.parent.name
        case_id = case_path.stem

        golden_prompt_path = prompts_dir / category / f"{case_id}.txt"

        case = load_json(case_path)

        esv = extract_engine_signal(
            case["stockfish_json"],
            fen=case["fen"],
        )

        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

        rendered = render_mode_2_prompt(
            system_prompt=SYSTEM_PROMPT,
            engine_signal=esv,
            rag_docs=rag_docs,
            fen=case["fen"],
            user_query=case.get("user_query", ""),
        )

        expected = golden_prompt_path.read_text(encoding="utf-8").strip()

        if rendered != expected:
            print("\n=== RENDERED ===\n")
            print(repr(rendered))
            print("\n=== EXPECTED ===\n")
            print(repr(expected))
            raise AssertionError(f"Snapshot mismatch for {case_path}")

    if failures:
        raise AssertionError("Golden prompt snapshot failures:\n" + json.dumps(failures, indent=2))
