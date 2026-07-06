"""Real-LLM smoke test against DeepSeek (Test Category C).

Verifies that:

  1. The production LLM provider (DeepSeek) is reachable from the test
     environment.
  2. A live DeepSeek response passes the same contract validators that
     gate production traffic (Mode-2 negative + output firewall, applied
     inside ``run_mode_2``).

Local-only by default — gated on ``RUN_DEEPSEEK_TESTS=1`` so a pytest
run from a contributor without an API key (or without billing wired up)
does not silently spend money or fail unrelated to the change.
``COACH_DEEPSEEK_API_KEY`` must be set in the environment; without it
the test skips with a clear message rather than crashing on a config
error inside ``call_llm``.

Runs in CI only on tag pushes / weekly cron via the workflow at
``.github/workflows/llm-regression-cron.yml`` and the tag-only job in
``.github/workflows/fly-deploy.yml``. Both jobs additionally gate on
the ``COACH_DEEPSEEK_API_KEY`` repo secret being present.

Replaces the legacy ``test_ollama_smoke.py`` (the project migrated from
a local Ollama sidecar to the managed DeepSeek API in May 2026; see
``docs/THREAT_MODEL.md`` and ``docker-compose.prod.yml`` for the
rationale). The contract is unchanged: a real LLM call, a real
validator pass, no golden expectations on text content.
"""

from __future__ import annotations

import os

import pytest

from llm.rag.llm.deepseek import DeepseekLLM
from llm.rag.llm.run_mode_2 import run_mode_2

# The instruction lines mirror the production prompt's rule
# (system_v2_mode_2.txt rule 5): the
# semantic gate REQUIREs "inevitable"/"forced" on a mate-type signal
# while the lexical gate FORBIDS "checkmate" / "mate in N" / "forced
# mate".  Without the instruction the live model routinely writes
# "checkmate", the repair loop rewrites it away, and the repaired text
# no longer carries the required inevitability vocabulary — the first
# genuine CI execution of this smoke test (2026-07-06) failed exactly
# that way.  The test's assertions are unchanged; a stub prompt that
# sets the model up to violate the validators tests the repair loop's
# worst case, not provider connectivity.
PROMPT = """
ENGINE SIGNAL:
evaluation.type = mate
forced mate

Explain the position.  The engine signal confirms the mate: state
plainly that the outcome is inevitable — use the word "inevitable" —
and never write the words "checkmate", "mate in N", or "forced mate".
"""

if os.getenv("RUN_DEEPSEEK_TESTS") != "1":
    pytest.skip(
        "DeepSeek smoke tests are disabled by default. "
        "Set RUN_DEEPSEEK_TESTS=1 and COACH_DEEPSEEK_API_KEY=sk-... to enable.",
        allow_module_level=True,
    )

if not os.getenv("COACH_DEEPSEEK_API_KEY", "").strip():
    pytest.skip(
        "COACH_DEEPSEEK_API_KEY is not set; cannot run real-LLM smoke test. "
        "Obtain a key at https://platform.deepseek.com and export it into the "
        "shell environment before re-running.",
        allow_module_level=True,
    )


def test_deepseek_forced_mate_smoke():
    """A live DeepSeek response on a forced-mate prompt must pass the
    validator gates.  Asserts only response shape — there is intentionally
    no golden expectation on the text content because the LLM is the
    only non-deterministic layer in the architecture."""
    llm = DeepseekLLM(temperature=0.2)

    # ESV mirroring the prompt's "forced mate" framing — the semantic
    # surface (now part of run_mode_2's chain) requires the LLM's output
    # to use "inevitable" or "forced" when evaluation.type == "mate", so
    # asserting on a real provider with type="mate" is the right shape for
    # a smoke test of provider connectivity + contract compliance.
    smoke_esv = {
        "evaluation": {"type": "mate", "value": 1, "band": "decisive_advantage"},
        "tactical_flags": ["mate_threat"],
    }

    response = run_mode_2(
        llm=llm,
        prompt=PROMPT,
        case_type="forced_mate",
        engine_signal=smoke_esv,
    )

    assert isinstance(response, str)
    assert len(response) > 20
