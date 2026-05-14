"""Real-LLM contract regression (Test Category D).

Repeats the Mode-2 contract validation across the full golden case
corpus and ``REPEATS`` invocations per case, against the production
LLM provider (DeepSeek). Designed to catch *stochastic* contract
violations — failures that would not surface in a single deterministic
fake-LLM pass but appear when real model sampling drifts.

Local-only by default — gated on ``RUN_DEEPSEEK_TESTS=1`` plus a real
``COACH_DEEPSEEK_API_KEY`` in env. Runs in CI only on tag pushes
(``.github/workflows/fly-deploy.yml`` ``llm-regression`` job) and the
weekly cron (``.github/workflows/llm-regression-cron.yml``), both gated
additionally on the ``COACH_DEEPSEEK_API_KEY`` repo secret.

Replaces the legacy Ollama-driven regression. The contract is
identical: same corpus, same validator gates, same REPEATS strategy —
only the BaseLLM adapter and provider differ.

Per ``docs/TESTING.md`` "LLM Regression Test Frequency", this test
MUST run before any release and after any system prompt / RAG corpus /
model change. Validator weakening to "fix" a failure here is
explicitly forbidden.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from llm.rag.documents import ALL_RAG_DOCUMENTS
from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.llm.deepseek import DeepseekLLM
from llm.rag.llm.run_mode_2 import run_mode_2
from llm.rag.prompts.mode_2.render import render_mode_2_prompt
from llm.rag.retriever.retriever import retrieve

ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = ROOT / "tests" / "golden" / "cases"

TEMPERATURE = 0.2
REPEATS = 3  # repeat to catch stochastic violations

if os.getenv("RUN_DEEPSEEK_TESTS") != "1":
    pytest.skip(
        "DeepSeek regression tests are disabled by default. "
        "Set RUN_DEEPSEEK_TESTS=1 and COACH_DEEPSEEK_API_KEY=sk-... to enable.",
        allow_module_level=True,
    )

if not os.getenv("COACH_DEEPSEEK_API_KEY", "").strip():
    pytest.skip(
        "COACH_DEEPSEEK_API_KEY is not set; cannot run real-LLM regression. "
        "Obtain a key at https://platform.deepseek.com and export it into the "
        "shell environment before re-running.",
        allow_module_level=True,
    )


def load_case(case_path: Path):
    with open(case_path, "r", encoding="utf-8") as f:
        return json.load(f)


def case_type_from_path(path: Path) -> str:
    return path.parent.name


def test_llm_regression_contract():
    """Run every ``case_001.json`` golden case through ``run_mode_2``
    ``REPEATS`` times against the live DeepSeek API.  Any contract
    violation raises inside ``run_mode_2`` (the validators are the
    contract), failing the test deterministically even when the
    underlying call is stochastic."""
    llm = DeepseekLLM(temperature=TEMPERATURE)

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
                engine_signal=esv,
            )
