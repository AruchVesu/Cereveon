"""Shared Mode-2 boundary-validator runner for /chat and /live/move pipelines.

Pinned invariant (project_mode_pipelines_validator_parity memory; PRs
#131 / #132):  every LLM-bearing Mode-2 path runs the same four boundary
gates in the same fixed order, inside the same retry loop.

Before this module existed, ``chat_pipeline._build_chat_llm`` and
``live_move_pipeline._build_hint_llm`` each owned a byte-identical
four-line validator block (issue #129-class drift channel).  Adding a
new gate, reordering, or skipping one of them in just one file
silently reopened the bug where a borderline LLM reply escaped the
pipeline retry loop and 500'd at the route boundary — surfaced on the
client as "Coach is offline" appearing after a successful LLM call.

Routing both pipelines through ``validate_mode_2_or_raise`` closes
that drift channel.  Source pinning lives in
``llm/tests/test_validator_parity.py``.

Exception lineage (used by the calling retry loops):

* ``OutputFirewallError`` from ``check_output``
    Prompt-leak / identity-disclosure / PII / harmful-content class.
    Callers MUST NOT retry — the LLM is leaking and a retry won't fix
    that.  Fall through to the deterministic builder.

* ``AssertionError`` from ``validate_mode_2_negative`` /
  ``validate_mode_2_structure``
    Forbidden vocabulary (engine, calculate, ...) or forbidden
    section structure (recommended move, plan, ...).  Callers SHOULD
    retry once with the retry-hint appended to the prompt; on
    exhaustion, fall through to deterministic.

* ``Mode2Violation`` from ``validate_mode_2_semantic``
    Semantic gate — equal-band vocabulary on an equal-band position,
    claimed advantage contradicting the engine, etc.  Same
    retry/fallback path as AssertionError.

Execution order is FIXED: firewall → negative → structure → semantic.
Reordering risks letting an OutputFirewallError-class violation
through AssertionError-class catch blocks in the calling retry loop.
"""

from __future__ import annotations

from llm.rag.safety.output_firewall import check_output as _check_output
from llm.rag.validators.mode_2_negative import (
    validate_mode_2_negative as _validate_neg,
)
from llm.rag.validators.mode_2_structure import (
    validate_mode_2_structure as _validate_struct,
)
from llm.rag.validators.mode_2_semantic import (
    validate_mode_2_semantic as _validate_sem,
)


def validate_mode_2_or_raise(response: str, engine_signal: dict) -> None:
    """Run every Mode-2 boundary gate on a candidate LLM response.

    Args:
        response: the LLM's raw output to validate.
        engine_signal: the engine-truth dict the response must respect
            (semantic gate reads ``engine_signal["evaluation"]["band"]``
            and friends — see ``validate_mode_2_semantic``).

    Raises:
        OutputFirewallError, AssertionError, or Mode2Violation —
        see module docstring for the calling-loop contract.
    """
    _check_output(response)
    _validate_neg(response)
    _validate_struct(response)
    _validate_sem(response, engine_signal)
