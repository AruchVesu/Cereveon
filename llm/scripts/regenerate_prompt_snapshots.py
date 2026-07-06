"""Regenerate the golden prompt snapshots (llm/tests/golden/prompts).

Uses the snapshot test's own canonical ``render_case`` so the script
and the test can never drift apart again (pre-2026-07-06 they had:
the script rendered v1 via ``render_mode_2`` while the test rendered
v1 via ``render_v1`` — regenerated files would not even have passed
the test).  Run from the repository root:

    python llm/scripts/regenerate_prompt_snapshots.py

Then commit the regenerated files TOGETHER with whatever prompt /
renderer / ESV / RAG change motivated them.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from llm.rag.tests.golden.test_prompt_snapshot import render_case  # noqa: E402

LLM_ROOT = REPO_ROOT / "llm"
CASES_DIR = LLM_ROOT / "tests" / "golden" / "cases"
PROMPTS_DIR = LLM_ROOT / "tests" / "golden" / "prompts"


def main():
    for case_path in CASES_DIR.rglob("case_*.json"):
        category = case_path.parent.name
        case_id = case_path.stem

        prompt_dir = PROMPTS_DIR / category
        prompt_dir.mkdir(parents=True, exist_ok=True)

        out_path = prompt_dir / f"{case_id}.txt"
        case = json.loads(case_path.read_text(encoding="utf-8"))
        out_path.write_text(render_case(case).strip() + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote snapshot: {out_path}")


if __name__ == "__main__":
    main()
