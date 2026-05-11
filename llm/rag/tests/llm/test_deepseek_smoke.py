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

PROMPT = """
ENGINE SIGNAL:
evaluation.type = mate
forced mate

Explain the position.
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

    response = run_mode_2(
        llm=llm,
        prompt=PROMPT,
        case_type="forced_mate",
    )

    assert isinstance(response, str)
    assert len(response) > 20
