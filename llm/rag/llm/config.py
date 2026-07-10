"""LLM pipeline runtime constants.

This module is the single source of truth for the Mode-2 validator
retry budget and the minimum quality score the in-pipeline quality
gate enforces.  Importing the constants here (rather than declaring
local copies in each caller) keeps the four LLM-bearing pipelines —
``llm.rag.llm.generate``, ``llm.seca.coach.explain_pipeline``,
``llm.seca.coach.live_move_pipeline``, ``llm.seca.coach.chat_pipeline``
— in lock-step on the retry budget.  Each pipeline still exposes a
locally-named alias (e.g. ``_CHAT_MAX_RETRIES``) so a reader grepping
for "live retry budget" or "chat retry budget" lands on the right
module, but the value flows from here.

Divergence is still possible: if a future pipeline needs a different
budget, its local alias can be set to a literal — the alias name
documents the choice.  Today they all share ``MAX_MODE_2_RETRIES``.

Documentation references
------------------------
- ``docs/ARCHITECTURE.md`` "Deterministic Fallback" — describes the
  retry-exhaustion path that fires after ``MAX_MODE_2_RETRIES``
  attempts fail validator gates.
- ``docs/THREAT_MODEL.md`` § T1 — names this constant in the
  prompt-injection defence stack.
- ``docs/OPERATIONS.md`` — names this constant in the LLM-failure
  operational runbook.
"""

#: Mode-2 validator retry budget.  Each pipeline's retry loop attempts
#: ``MAX_MODE_2_RETRIES + 1`` total LLM calls before falling through to
#: the deterministic builder.  Two retries balances three concerns:
#: (a) latency budget (each retry is another DeepSeek round-trip);
#: (b) cost per request; (c) likelihood that a third attempt produces
#: a different result than the second.  Pinned by
#: ``llm/rag/tests/test_run_mode_2_cascades.py`` and
#: ``llm/rag/tests/test_run_mode_2_additional.py``.
MAX_MODE_2_RETRIES = 2

#: Quality-gate minimum score (0-10).  Replies scoring below this are
#: subject to one retry on the quality-heuristic path (Category E,
#: advisory only — not a safety gate).
MIN_QUALITY_SCORE = 7

#: Worst-case output-spend bounds, sent as ``max_tokens`` by the
#: PRODUCTION call sites only (the BaseLLM adapter and the smoke /
#: regression harnesses stay uncapped so their wire shape is unchanged).
#: Without a cap the only output bound is the 100 kB defense-in-depth
#: byte cap in ``explain_pipeline`` — ~25k tokens of billable runaway
#: per pathological call.  The caps are cost insurance, not shaping:
#: each sits several times above its pipeline's observed reply length,
#: so a normal generation never touches it.  A capped-off outlier is
#: handled by the existing contract machinery (validators → targeted
#: retry → deterministic fallback), the same path an uncapped rambling
#: reply would have taken anyway.
#:
#: Sizing (completion_tokens histogram, 2026-07): Mode-2 chat replies
#: run ~150-250 tokens ("2-4 short paragraphs"); 500 is ~2x the
#: observed ceiling.  Mode-1 hints are 1-2 sentences (~40 tokens);
#: 160 is ~4x.  Raise, never lower, in response to REQUIRE-gate
#: truncation failures showing up in the weekly regression run.
CHAT_MAX_COMPLETION_TOKENS = 500
MODE_1_MAX_COMPLETION_TOKENS = 160
