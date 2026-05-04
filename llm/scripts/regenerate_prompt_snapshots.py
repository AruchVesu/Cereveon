from pathlib import Path
import json

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.prompts.render_mode_2 import render_mode_2_prompt

ROOT = Path(".")
CASES_DIR = ROOT / "tests/golden/cases"
PROMPTS_DIR = ROOT / "tests/golden/prompts"

SYSTEM_PROMPT = (ROOT / "rag/prompts/mode_2/system_v1.txt").read_text(encoding="utf-8")


def main():
    for case_path in CASES_DIR.rglob("case_*.json"):
        category = case_path.parent.name
        case_id = case_path.stem

        prompt_dir = PROMPTS_DIR / category
        prompt_dir.mkdir(parents=True, exist_ok=True)

        out_path = prompt_dir / f"{case_id}.txt"

        case = json.loads(case_path.read_text(encoding="utf-8"))

        esv = extract_engine_signal(case.get("stockfish_json", {}))
        rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

        rendered = render_mode_2_prompt(
            system_prompt=SYSTEM_PROMPT,
            engine_signal=esv,
            rag_docs=rag_docs,
            fen=case["fen"],
            user_query=case.get("user_query", ""),
        )

        out_path.write_text(rendered.strip(), encoding="utf-8")
        print(f"Wrote snapshot: {out_path}")


if __name__ == "__main__":
    main()
