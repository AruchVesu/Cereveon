from pathlib import Path
from llm.rag.tests.golden.test_prompt_snapshot import (
    render_mode_2_prompt,
    extract_engine_signal,
    load_json,
    ALL_RAG_DOCUMENTS,
    retrieve,
    SYSTEM_PROMPT,
)

ROOT = Path(__file__).resolve().parents[3]

cases_dir = ROOT / "rag" / "tests" / "golden" / "cases"
prompts_dir = ROOT / "rag" / "tests" / "golden" / "prompts"

for case_path in cases_dir.rglob("case_*.json"):
    category = case_path.parent.name
    case_id = case_path.stem

    out_dir = prompts_dir / category
    out_dir.mkdir(parents=True, exist_ok=True)

    case = load_json(case_path)
    esv = extract_engine_signal(case["stockfish_json"])
    rag_docs = retrieve(esv, ALL_RAG_DOCUMENTS)

    rendered = render_mode_2_prompt(
        system_prompt=SYSTEM_PROMPT,
        engine_signal=esv,
        rag_docs=rag_docs,
        fen=case["fen"],
        user_query=case.get("user_query", ""),
    )

    (out_dir / f"{case_id}.txt").write_text(
        rendered.strip(),
        encoding="utf-8",
    )

print("✅ Golden snapshots regenerated")
