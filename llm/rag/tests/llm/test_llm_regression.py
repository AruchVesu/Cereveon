"""Real-LLM contract regression (Test Category D).

Repeats the Mode-2 contract validation across the full golden case
corpus and ``REPEATS`` invocations per case, against the production
LLM provider (DeepSeek). Designed to catch *stochastic* contract
violations — failures that would not surface in a single deterministic
fake-LLM pass but appear when real model sampling drifts.

The prompt under test is the PRODUCTION composition: each case is
rendered through ``test_prompt_snapshot.render_case`` — the live
``system_v2_mode_2.SYSTEM_PROMPT`` plus the ``render_mode_2`` renderer,
exactly what ``llm/rag/deploy/embedded.py`` sends on the ``run_mode_2``
surface, byte-identical to what the golden snapshot pins.  (Until
2026-07-06 this harness exercised ``mode_2/system_v1.txt`` — a prompt
with zero production consumers; the v1/v2 fork meant the weekly LLM
budget regression-tested dead text while the production prompt drifted
untested.  v1 is retired.)

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

from llm.rag.engine_signal.extract_engine_signal import extract_engine_signal
from llm.rag.llm.deepseek import DeepseekLLM
from llm.rag.llm.run_mode_2 import run_mode_2
from llm.rag.tests.golden.test_prompt_snapshot import render_case

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

        # ``render_case`` is the canonical golden-case render — the
        # production v2 system prompt + the embedded-surface renderer,
        # byte-identical to what the prompt snapshot pins.  It renders a
        # missing-data case (EMPTY stockfish_json) with the verbatim
        # empty signal so the model can see the absence it must
        # acknowledge; ``extract_engine_signal`` would otherwise
        # backfill a complete-looking equal evaluation and make the
        # missing-data REQUIRE unreachable.
        prompt = render_case(case)
        esv = extract_engine_signal(case.get("stockfish_json", {}))

        for _ in range(REPEATS):
            run_mode_2(
                llm=llm,
                prompt=prompt,
                case_type=case_type,
                engine_signal=esv,
            )


# ---------------------------------------------------------------------------
# Production /chat-path stability (added 2026-06-07).
# ---------------------------------------------------------------------------
# ``test_llm_regression_contract`` above exercises ``run_mode_2`` + the v1
# prompt.  Production /chat uses ``generate_chat_reply`` + system_v2_mode_2 +
# the word-boundary semantic validators.  This case guards THAT path against
# model drift / validator re-tightening: when the gates over-reject ordinary
# coaching, the pipeline silently falls through to the templated
# deterministic reply.  Real-answer rate was ~25% before the 2026-06
# over-rejection fixes and ~100% (30/30 across a position matrix) after.
# Deterministic counterpart: llm/tests/test_coaching_not_overrejected.py.

_STABILITY_FENS = [
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2P5/2N2N2/PP1PPPPP/R1BQKB1R w KQkq - 4 4",  # equal opening
    "4k3/8/8/8/8/8/4PPPP/R3K2R w KQ - 0 1",                                   # decisive advantage
    "8/5k2/8/4K3/4P3/8/8/8 w - - 0 1",                                        # king + pawn endgame
]
_STABILITY_QS = [
    "Why is this bad for my king?",
    "What's my plan here?",
    "Is there a tactic I should look for?",
    "How do I improve my position?",
]
# Healthy is ~100%; a real over-rejection regression collapses toward ~25%.
# The threshold tolerates rare stochastic fallbacks while catching a
# collapse.
_MIN_REAL_RATE = 0.80


def test_llm_chat_path_not_overrejected():
    """Production ``generate_chat_reply`` must return REAL LLM answers (not
    the templated deterministic fallback) for ordinary coaching questions
    across a position matrix.  Guards the live /chat path against validator
    over-rejection and model drift."""
    from llm.seca.coach import chat_pipeline as cp
    from llm.seca.coach.chat_pipeline import ChatTurn, generate_chat_reply

    fell_back = {"hit": False}
    orig = cp._build_reply_deterministic

    def _spy(*args, **kwargs):
        fell_back["hit"] = True
        return orig(*args, **kwargs)

    cp._build_reply_deterministic = _spy
    total = 0
    real = 0
    fallbacks: list[tuple[str, str]] = []
    try:
        for fen in _STABILITY_FENS:
            for q in _STABILITY_QS:
                fell_back["hit"] = False
                generate_chat_reply(fen, [ChatTurn(role="user", content=q)])
                total += 1
                if fell_back["hit"]:
                    fallbacks.append((fen[:24], q))
                else:
                    real += 1
    finally:
        cp._build_reply_deterministic = orig

    rate = real / total if total else 0.0
    assert rate >= _MIN_REAL_RATE, (
        f"Mode-2 /chat over-rejection regression: only {real}/{total} "
        f"({rate:.0%}) coaching questions got a real LLM answer "
        f"(threshold {_MIN_REAL_RATE:.0%}).  Fallbacks: {fallbacks}.  A "
        f"collapsed rate means a validator was re-tightened or the model "
        f"drifted — grep 'Mode-2 LLM failed after' for the rejected token."
    )
