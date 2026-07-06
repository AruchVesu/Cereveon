"""Golden prompt snapshots — pin the PRODUCTION Mode-2 prompt composition.

Renders every golden case through the exact pieces the ``run_mode_2``
production surface uses (``llm/rag/deploy/embedded.py``'s
``explain_position``): the live ``SYSTEM_PROMPT`` from
``llm.rag.prompts.system_v2_mode_2`` and the plain-label renderer
``llm.rag.prompts.render_mode_2``.  Any change to the production system
prompt, the renderer, the ESV extraction, or RAG retrieval therefore
breaks this snapshot and forces a deliberate regeneration
(``llm/scripts/regenerate_prompt_snapshots.py``) in the same commit.

History: until 2026-07-06 this test pinned ``mode_2/system_v1.txt`` via
``render_v1`` — a prompt/renderer pair with ZERO production consumers
(the v1/v2 fork).  Production ``system_v2_mode_2.txt`` could be
rewritten without breaking a single test while the weekly regression
suite contract-tested the dead v1 prompt.  Both v1 files are retired;
this snapshot now guards what actually runs.

A case whose ``stockfish_json`` is EMPTY declares a missing-data
premise: the prompt renders (and retrieves against) the verbatim empty
signal so the model can see the absence it must acknowledge — the same
rule the Category D harness applies (see test_llm_regression.py).
"""

import json
from pathlib import Path

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.retriever.retriever import retrieve
from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.prompts.render_mode_2 import render_mode_2_prompt
from llm.rag.prompts.system_v2_mode_2 import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def render_case(case: dict) -> str:
    """Canonical golden-case render — shared shape with the Category D
    harness and the regeneration script.  Mirrors ``embedded.py``:
    extract without a fen kwarg, production system prompt, plain-label
    renderer.  Empty ``stockfish_json`` renders verbatim ``{}``."""
    esv = extract_engine_signal(case.get("stockfish_json", {}))
    signal_for_prompt = esv if case.get("stockfish_json") else {}
    rag_docs = retrieve(signal_for_prompt, ALL_RAG_DOCUMENTS)
    return render_mode_2_prompt(
        system_prompt=SYSTEM_PROMPT,
        engine_signal=signal_for_prompt,
        rag_docs=rag_docs,
        fen=case["fen"],
        user_query=case.get("user_query", ""),
    )


def test_all_golden_prompt_snapshots():
    cases_dir = ROOT / "tests" / "golden" / "cases"
    prompts_dir = ROOT / "tests" / "golden" / "prompts"

    for case_path in cases_dir.rglob("case_*.json"):
        category = case_path.parent.name
        case_id = case_path.stem

        golden_prompt_path = prompts_dir / category / f"{case_id}.txt"

        rendered = render_case(load_json(case_path)).strip()
        expected = golden_prompt_path.read_text(encoding="utf-8").strip()

        if rendered != expected:
            print("\n=== RENDERED ===\n")
            print(repr(rendered))
            print("\n=== EXPECTED ===\n")
            print(repr(expected))
            raise AssertionError(f"Snapshot mismatch for {case_path}")


def test_snapshot_pins_the_production_prompt():
    """The whole point of the 2026-07-06 convergence: the snapshot must
    embed the LIVE production system prompt, so a v2 rewrite cannot be
    test-silent again.  Asserts the golden files contain a distinctive
    line of the current production prompt text."""
    prompts_dir = ROOT / "tests" / "golden" / "prompts"
    marker = SYSTEM_PROMPT.strip().splitlines()[0]
    snapshot_files = list(prompts_dir.rglob("case_*.txt"))
    assert snapshot_files, "no golden prompt snapshots found"
    for snap in snapshot_files:
        assert marker in snap.read_text(encoding="utf-8"), (
            f"{snap} does not embed the production system prompt "
            f"(missing marker line {marker!r}) — regenerate via "
            f"llm/scripts/regenerate_prompt_snapshots.py"
        )
