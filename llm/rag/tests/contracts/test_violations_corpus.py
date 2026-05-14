"""
Validator violations corpus — auditable contract test.

For every entry in fixtures/violations.jsonl, this test dispatches the
violation text to the production validator surface that's responsible for
catching it and asserts the right exception is raised.

The corpus is the externally-readable evidence backing the README's claim
that "the LLM is never trusted" and "all LLM outputs pass contract validation
before being returned." A reviewer can open violations.jsonl, read each
exemplar, and run this test to confirm the contract still rejects each one.

Surfaces covered:
  - run_mode_2  (used by /explain) — composes validate_mode_2_negative,
    validate_mode_2_structure, and contracts.validate_output. Wrapping the
    text in a FakeLLM-derived stub lets us drive the production assembly
    without invoking a real model; FakeLLM bypasses the retry/repair loop so
    the contract failure surfaces immediately as AssertionError.
  - validate_mode_2_semantic  (used by generate_with_adaptive_retry) —
    called directly with text + engine_signal; raises Mode2Violation.
  - output_firewall.check_output  (used by /chat and /explain post-LLM) —
    called directly with text; raises OutputFirewallError carrying a
    category string.

The trailing test test_corpus_covers_every_advertised_rule keeps the corpus
honest: every rule the README and TESTING.md advertise as enforced must have
at least one violation entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm.rag.llm.fake import FakeLLM
from llm.rag.llm.run_mode_2 import run_mode_2
from llm.rag.safety.output_firewall import (
    OutputFirewallError,
    check_output,
)
from llm.rag.validators.mode_2_semantic import (
    Mode2Violation,
    validate_mode_2_semantic,
)

_CORPUS_PATH = Path(__file__).resolve().parent / "fixtures" / "violations.jsonl"
_DUMMY_PROMPT = "dummy prompt — FakeLLM stub ignores its argument"

# Neutral ESV for the run_mode_2 surface entries.  Disables every ESV-gated
# semantic check so each corpus entry's ``expected_error_substring`` remains
# pinned to the validator that owns its rule (negative / structure / output) —
# inserting semantic between structure and output would otherwise shift which
# validator fires on entries like SPE-01 ("likely") if ESV activated the
# semantic surface.  Semantic-surface entries (SEM-*) bypass this dict
# because they're dispatched directly to ``validate_mode_2_semantic`` with
# their own ESV declared in the fixture.
_NEUTRAL_ESV = {
    "evaluation": {"type": "cp", "value": 0},
    "tactical_flags": ["any"],
}


def _load_corpus() -> list[dict]:
    entries: list[dict] = []
    with _CORPUS_PATH.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            entries.append(json.loads(line))
    if not entries:
        raise RuntimeError(f"violations corpus at {_CORPUS_PATH} is empty")
    return entries


_CORPUS = _load_corpus()


class _CorpusLLM(FakeLLM):
    """FakeLLM subclass that returns a fixed corpus exemplar.

    Inheriting from FakeLLM is load-bearing: run_mode_2 checks
    isinstance(llm, FakeLLM) to bypass its retry/repair loop and surface the
    validator failure immediately (see llm/rag/llm/run_mode_2.py around the
    'For FakeLLM, preserve immediate failure behavior' branch). The contract
    test needs the raw failure, not the production self-healing path.
    """

    def __init__(self, text: str) -> None:
        super().__init__(mode="raw")
        self._text = text

    def generate(self, prompt: str) -> str:
        return self._text


def _assert_error_contains(entry: dict, exc_info: pytest.ExceptionInfo) -> None:
    """Pin which validator's error message fires.

    Asserting only that *some* exception is raised (e.g. ``pytest.raises
    (AssertionError)``) lets a regression that swaps which validator catches a
    given violation slip through unnoticed.  The ``expected_error_substring``
    field is a deliberate downstream-of-the-rule check: a fix that "fires the
    wrong validator on the right input" is a regression we want to catch.
    """
    expected = entry["expected_error_substring"]
    actual = str(exc_info.value)
    assert expected.lower() in actual.lower(), (
        f"{entry['id']}: expected error message to contain {expected!r}, " f"got: {actual!r}"
    )


@pytest.mark.parametrize("entry", _CORPUS, ids=lambda e: e["id"])
def test_corpus_entry_is_blocked_by_contract(entry: dict) -> None:
    """Every corpus entry must be rejected by the surface that owns its rule.

    Each entry pins both the exception TYPE and a substring of the error
    MESSAGE so a refactor that switches which validator catches the
    violation also fails this test, even when some validator still fires.

    Each branch uses a distinct ExceptionInfo variable name so mypy can
    narrow the exception type per surface (rebinding ``exc_info`` across
    branches with three different exception classes is a type-error under
    the validator-surface strict mypy override).
    """
    surface = entry["surface"]
    text = entry["text"]

    if surface == "run_mode_2":
        case_type = entry["case_type"]
        with pytest.raises(AssertionError) as assertion_info:
            run_mode_2(
                llm=_CorpusLLM(text),
                prompt=_DUMMY_PROMPT,
                case_type=case_type,
                engine_signal=_NEUTRAL_ESV,
            )
        _assert_error_contains(entry, assertion_info)
    elif surface == "validate_mode_2_semantic":
        engine_signal = entry["engine_signal"]
        with pytest.raises(Mode2Violation) as semantic_info:
            validate_mode_2_semantic(text, engine_signal)
        _assert_error_contains(entry, semantic_info)
    elif surface == "output_firewall":
        expected_category = entry["expected_firewall_category"]
        with pytest.raises(OutputFirewallError) as firewall_info:
            check_output(text)
        assert firewall_info.value.category == expected_category, (
            f"{entry['id']}: expected firewall category {expected_category!r}, "
            f"got {firewall_info.value.category!r}"
        )
        _assert_error_contains(entry, firewall_info)
    else:
        pytest.fail(f"{entry['id']}: unknown surface dispatch {surface!r}")


def test_every_entry_carries_expected_error_substring() -> None:
    """Schema invariant: every corpus entry must declare expected_error_substring.

    Surfaces this gap at corpus-load time rather than at parametrised dispatch
    time, where a missing field would surface as an ID-specific KeyError —
    harder to diagnose and harder to fix in bulk.
    """
    missing = [entry["id"] for entry in _CORPUS if not entry.get("expected_error_substring")]
    assert not missing, (
        "Corpus entries missing expected_error_substring: "
        f"{missing}. Each entry must declare a substring of the error message "
        "the firing validator produces — see violations.jsonl for examples."
    )


def test_corpus_covers_every_advertised_safety_rule() -> None:
    """Sanity gate: every rule the README + TESTING.md advertise must have a row.

    If a new rule lands in the validators (or an advertised rule is removed),
    update both the corpus and the required_rules set below — this test is
    the canonical link between the prose claims and the corpus evidence.
    """
    rules_present = {entry["rule"] for entry in _CORPUS}

    # High-level rule families exercised across all surfaces. Sub-variant rules
    # (e.g. "move suggestion (notation)" vs "move suggestion (advisory)") are
    # collapsed by family for this gate; the parametrised test above covers
    # every individual ID.
    families: dict[str, set[str]] = {
        "engine mention": {"engine mention", "engine mention + move suggestion"},
        "move suggestion": {
            "move suggestion (notation)",
            "move suggestion (advisory)",
            "engine mention + move suggestion",
            "speculative + advisory + mate misframing",
        },
        "invented tactics": {"invented tactics"},
        "mate misframing": {
            "mate misframing",
            "mate misframing (missing required phrase)",
            "speculative + advisory + mate misframing",
        },
        "missing-data refusal": {"missing-data refusal"},
        "speculative language": {
            "speculative language",
            "speculative + advisory + mate misframing",
        },
        "invented evaluation": {"invented evaluation"},
        "prompt leakage": {"prompt leakage"},
        "role bypass": {"role bypass"},
        "identity confusion": {"identity confusion"},
        "PII / credential leakage": {"PII leakage", "credential leakage"},
        "harmful instructions": {"harmful instructions"},
    }

    missing = [family for family, members in families.items() if not members & rules_present]
    assert not missing, (
        "violations.jsonl does not cover advertised safety rule families: "
        f"{missing}. Add at least one entry per missing family."
    )
