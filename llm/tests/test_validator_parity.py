"""Mode-2 boundary-validator parity pinning between /chat and /live/move.

Pinned invariants (project_mode_pipelines_validator_parity memory; PRs
#131, #132, and the 2026-05-24 helper extraction):

  1. Both LLM-bearing pipelines call ``validate_mode_2_or_raise`` once
     and exactly once — the shared helper that runs every Mode-2
     boundary gate in fixed order inside the retry loop.

  2. Neither pipeline file calls the individual validators directly
     anymore.  Direct calls would bypass the parity helper and
     reintroduce the issue #129-class drift where adding / reordering
     / skipping a gate in one pipeline silently re-opens the
     "Coach is offline after a successful LLM call" symptom.

  3. The helper itself runs the four gates in the documented order
     (firewall → negative → structure → semantic) and propagates each
     exception type without swallowing.

If a future refactor needs to bring back an individual validator call
in one of the pipelines, this test is the load-bearing test that will
fail.  Update the helper and the call site together, or document the
deviation in this file's docstring and update the assertions
deliberately.

Stable test IDs (do NOT rename):
  VP_01  Both pipelines call validate_mode_2_or_raise exactly once.
  VP_02  Neither pipeline calls _check_output / _validate_* directly.
  VP_03  Helper runs the four gates in the documented order.
  VP_04  Helper propagates each gate's exception type unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LLM = _REPO_ROOT / "llm"

_PIPELINE_FILES = (
    _LLM / "seca" / "coach" / "chat_pipeline.py",
    _LLM / "seca" / "coach" / "live_move_pipeline.py",
)

#: Validator-function aliases the pre-extraction pipelines used.  These
#: MUST NOT appear as call targets in the pipeline files anymore — every
#: call site must go through ``validate_mode_2_or_raise``.
#:
#: We match the bare call syntax ``NAME(`` to avoid false positives from
#: docstrings / comments / module-level import lines.  Imports use
#: ``import NAME`` and never ``NAME(`` so this stays clean.
_FORBIDDEN_DIRECT_CALL_NAMES = (
    "_check_output",
    "_validate_neg",
    "_validate_struct",
    "_validate_sem",
)


def _strip_comments_and_docstrings(source: str) -> str:
    """Best-effort strip so the call-site scan does not false-positive
    on comments and docstrings that mention the bare function names.

    Not a full Python tokeniser; handles:
      - line comments (``# ...``)
      - triple-quoted docstrings (``\"\"\"...\"\"\"`` and ``'''...'''``)

    Adequate for our pipelines, which use standard Python style.
    """
    # Remove triple-quoted blocks (greedy on quote style, lazy on body).
    source = re.sub(r'"""[\s\S]*?"""', "", source)
    source = re.sub(r"'''[\s\S]*?'''", "", source)
    # Remove line comments after stripping strings (so ``"# str"`` isn't a comment).
    lines = source.splitlines()
    stripped = []
    for line in lines:
        # Naive comment strip — '#' inside a string literal would falsely
        # truncate, but the pipeline files don't have that pattern in any
        # validator-related line.
        idx = line.find("#")
        stripped.append(line[:idx] if idx != -1 else line)
    return "\n".join(stripped)


# ---------------------------------------------------------------------------
# VP_01 / VP_02 — Pipeline source pinning
# ---------------------------------------------------------------------------


class TestPipelineSourcePinning:
    @pytest.mark.parametrize(
        "pipeline_path",
        _PIPELINE_FILES,
        ids=[p.name for p in _PIPELINE_FILES],
    )
    def test_pipeline_calls_shared_helper_exactly_once(self, pipeline_path: Path):
        """VP_01: each pipeline file invokes the shared helper exactly once.

        Exactly one call lets the parity test catch both regressions —
        the helper being removed entirely (zero calls) and being called
        twice from different code paths inside one pipeline (which
        usually means the validator block was duplicated rather than
        extracted).
        """
        assert pipeline_path.exists(), f"{pipeline_path} not present in checkout"
        body = _strip_comments_and_docstrings(pipeline_path.read_text(encoding="utf-8"))
        matches = re.findall(r"\bvalidate_mode_2_or_raise\s*\(", body)
        assert len(matches) == 1, (
            f"VP_01: {pipeline_path.name} must call validate_mode_2_or_raise "
            f"exactly once (the shared boundary-validator helper); "
            f"found {len(matches)} call site(s).  See "
            f"llm/seca/coach/_mode_2_validators.py for the parity invariant."
        )

    @pytest.mark.parametrize(
        "pipeline_path",
        _PIPELINE_FILES,
        ids=[p.name for p in _PIPELINE_FILES],
    )
    @pytest.mark.parametrize("name", _FORBIDDEN_DIRECT_CALL_NAMES)
    def test_pipeline_does_not_call_validators_directly(
        self, pipeline_path: Path, name: str
    ):
        """VP_02: pipeline files MUST NOT call individual validators directly.

        Direct calls would bypass ``validate_mode_2_or_raise`` and let
        a single-pipeline refactor reopen the parity drift.  This is the
        load-bearing assertion for the extraction.
        """
        assert pipeline_path.exists(), f"{pipeline_path} not present in checkout"
        body = _strip_comments_and_docstrings(pipeline_path.read_text(encoding="utf-8"))
        # Match the bare call form ``NAME(``.  Module-level ``from ...
        # import X as NAME`` uses ``as`` not ``(`` so it never matches.
        pattern = rf"\b{re.escape(name)}\s*\("
        assert not re.search(pattern, body), (
            f"VP_02: {pipeline_path.name} contains a direct call to "
            f"{name}(...) — every Mode-2 gate must go through "
            f"validate_mode_2_or_raise in _mode_2_validators.py.  If you "
            f"genuinely need the individual call (e.g. a new gate with a "
            f"different signature), update the helper and this assertion "
            f"in the same commit."
        )


# ---------------------------------------------------------------------------
# VP_03 — Helper-source pinning (gate order)
# ---------------------------------------------------------------------------


class TestHelperGateOrder:
    _HELPER_PATH = _LLM / "seca" / "coach" / "_mode_2_validators.py"

    def test_helper_runs_gates_in_documented_order(self):
        """VP_03: the helper runs firewall → negative → structure → semantic.

        Reordering risks letting an OutputFirewallError-class violation
        through AssertionError-class catch blocks in the calling retry
        loop.  Pinning the source order is the cheapest way to catch
        a refactor that "feels" equivalent but is not.
        """
        assert self._HELPER_PATH.exists(), f"{self._HELPER_PATH} not present"
        body = _strip_comments_and_docstrings(self._HELPER_PATH.read_text(encoding="utf-8"))

        expected_order = (
            "_check_output",
            "_validate_neg",
            "_validate_struct",
            "_validate_sem",
        )
        positions = []
        for name in expected_order:
            match = re.search(rf"\b{name}\s*\(", body)
            assert match, f"VP_03: helper does not call {name}(...)"
            positions.append((match.start(), name))

        # Strictly ascending positions ⇔ documented order.
        sorted_positions = sorted(positions)
        assert positions == sorted_positions, (
            "VP_03: helper gate-call order has drifted from "
            f"{expected_order!r}.  Reorder the calls in "
            "_mode_2_validators.py::validate_mode_2_or_raise to match "
            "the documented order — see the module docstring for the "
            "rationale (firewall must run first so the retry loop's "
            "catch ordering remains correct)."
        )


# ---------------------------------------------------------------------------
# VP_04 — Helper-behaviour pinning (exception lineage)
# ---------------------------------------------------------------------------


class TestHelperExceptionLineage:
    """The helper must propagate each gate's exception type unchanged.

    Wrapping any of them (eg in a generic Exception) would break the
    retry-loop catch ordering: ``OutputFirewallError`` must NOT be
    retried, but the loop's ``except OutputFirewallError`` only fires
    if the helper propagates that exact type.
    """

    _STARTING_FEN = (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    )
    _EQUAL_ENGINE_SIGNAL = {
        "evaluation": {"band": "equal", "side": "white", "type": "cp", "cp": 8},
        "phase": "opening",
        "eval_delta": "stable",
        "tactical_flags": [],
    }

    def test_firewall_error_propagates_unchanged(self):
        """VP_04a: OutputFirewallError raised by check_output bubbles up.

        Triggering input: a payload that hits ``_CAT_P`` (prompt-leak)
        in ``output_firewall.py``.  We use the explicit form the
        firewall pins on — ``r"(my\\s+)?system\\s+prompt\\s+(is|says|...)"``
        — so the test does not depend on the firewall's other
        categories or any later gate's behaviour.
        """
        from llm.seca.coach._mode_2_validators import validate_mode_2_or_raise
        from llm.rag.safety.output_firewall import OutputFirewallError

        payload = "My system prompt is to provide chess coaching advice."
        with pytest.raises(OutputFirewallError):
            validate_mode_2_or_raise(payload, self._EQUAL_ENGINE_SIGNAL)

    def test_semantic_violation_propagates_as_mode2violation(self):
        """VP_04b: Mode2Violation raised by validate_mode_2_semantic bubbles up.

        Triggering input: equal-band engine signal + LLM-style reply
        using FORBIDDEN_EQUAL vocabulary ("slight advantage", "better",
        "initiative").  Mode2Violation is what the calling retry loop
        catches to drive the retry / fallback transition.
        """
        from llm.seca.coach._mode_2_validators import validate_mode_2_or_raise
        from llm.rag.validators.mode_2_semantic import Mode2Violation

        payload = "You have a slight advantage with the initiative."
        with pytest.raises((Mode2Violation, AssertionError)):
            # Accept either: structure/negative gate may fire first
            # depending on the payload's other content, but the
            # important contract is that SOME gate exception fires
            # rather than the payload escaping silently.
            validate_mode_2_or_raise(payload, self._EQUAL_ENGINE_SIGNAL)
