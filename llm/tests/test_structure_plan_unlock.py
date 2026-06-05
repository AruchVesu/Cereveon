"""
Regression tests for narrowing ``\\bplan\\b`` → ``\\bplan\\b\\s*:`` in the
Mode-2 structural filter ``MOVE_ADVISORY_PATTERNS`` (2026-06-04).

Context
-------
The bare ``\\bplan\\b`` pattern rejected EVERY occurrence of the word
"plan" anywhere in a Mode-2 reply.  But "plan" is core coaching
vocabulary — "your plan is to improve your worst piece", "White's plan
involves central control" — and the Mode-2 system prompt itself invites
it ("discuss themes, plans, principles").  So the LLM did as instructed,
tripped ``validate_mode_2_structure`` on every retry, and the chat /
live-move pipelines fell through to the templated deterministic
fallback — the "templated chat" UX report.  This is the same
false-positive shape as ``\\bshould\\b`` (PR #170,
test_speculative_should_unlock.py).

The forbidden shape was never the strategic noun.  It was always the
prescriptive HEADER form — "Plan: trade pieces and convert." — a
labelled course of action that is a move-suggestion in disguise
(DUAL_USE_TOKENS["plan"] described exactly this).  Narrowing to
``\\bplan\\b\\s*:`` keeps the header rejected while letting the noun
through.

Pinned invariants
-----------------
 1. PLAN_BARE_NOT_IN_MOVE_ADVISORY — bare ``\\bplan\\b`` is gone; the
                                     colon-anchored form is present.
 2. PLAN_NOUN_PASSES_STRUCTURE     — strategic-noun "plan" sentences
                                     pass validate_mode_2_structure
                                     (and the lexical gate).
 3. PLAN_HEADER_STILL_REJECTED     — "Plan:" header sentences still
                                     fail validate_mode_2_structure.
 4. PLAN_REMAINS_DOCUMENTED        — DUAL_USE_TOKENS keeps the rationale.
"""

from __future__ import annotations

import pytest

from llm.rag.validators._fixtures import DUAL_USE_PASSING_SAMPLES
from llm.rag.validators._rules import DUAL_USE_TOKENS, MOVE_ADVISORY_PATTERNS
from llm.rag.validators.mode_2_negative import validate_mode_2_negative
from llm.rag.validators.mode_2_structure import (
    FORBIDDEN_SECTIONS,
    validate_mode_2_structure,
)


# ---------------------------------------------------------------------------
# Pin 1 — the bare regex is gone; only the header form remains
# ---------------------------------------------------------------------------


class TestPlanBareNotInMoveAdvisory:
    """The bare ``\\bplan\\b`` regex MUST NOT be in MOVE_ADVISORY_PATTERNS.
    Re-adding it re-opens the over-rejection that forced the templated
    deterministic fallback."""

    def test_PLAN_BARE_NOT_IN_MOVE_ADVISORY(self):
        assert r"\bplan\b" not in MOVE_ADVISORY_PATTERNS, (
            "Bare `\\bplan\\b` was re-added to MOVE_ADVISORY_PATTERNS.  It "
            "over-rejects the strategic noun ('your plan is to ...') that "
            "the Mode-2 system prompt explicitly invites, dropping every "
            "plan-mentioning reply to the templated fallback.  Use the "
            "header form `\\bplan\\b\\s*:` instead — see "
            "test_PLAN_HEADER_STILL_REJECTED for the inverse pin."
        )

    def test_plan_header_form_is_present(self):
        assert r"\bplan\b\s*:" in MOVE_ADVISORY_PATTERNS, (
            "The narrowed header form `\\bplan\\b\\s*:` is missing from "
            "MOVE_ADVISORY_PATTERNS — the 'Plan:' prescriptive-section "
            "guard has been lost entirely.  Restore it."
        )
        # FORBIDDEN_SECTIONS is the structure validator's view of the
        # same list — keep them in lock-step.
        assert r"\bplan\b\s*:" in FORBIDDEN_SECTIONS

    def test_plan_remains_documented_as_dual_use(self):
        """DUAL_USE_TOKENS MUST keep documenting why "plan" is treated
        asymmetrically.  Dropping the entry loses the rationale the next
        contributor needs before touching the pattern."""
        assert "plan" in DUAL_USE_TOKENS, (
            "`plan` is missing from DUAL_USE_TOKENS in _rules.py.  Keep "
            "the registry entry — it records why only the 'Plan:' header "
            "form is forbidden while the strategic noun is accepted."
        )
        assert DUAL_USE_TOKENS["plan"]["enforced_at"] == "structural-header", (
            "DUAL_USE_TOKENS['plan'] must record "
            "enforced_at='structural-header' — only the colon-anchored "
            "header form is enforced now, not the bare word."
        )


# ---------------------------------------------------------------------------
# Pin 2 — strategic-noun 'plan' coaching passes the structure + lexical gates
# ---------------------------------------------------------------------------


class TestPlanNounCoachingAccepted:
    """Coaching sentences using "plan" as a strategic noun must pass
    ``validate_mode_2_structure`` — this is the high-bar pin that
    re-introducing the bare ``\\bplan\\b`` would break.

    Samples are the canonical strategic-noun list from
    ``DUAL_USE_PASSING_SAMPLES['plan']`` so the lexical-surface taxonomy
    test and this structural-surface test stay in agreement."""

    @pytest.mark.parametrize("phrase", DUAL_USE_PASSING_SAMPLES["plan"])
    def test_PLAN_NOUN_PASSES_STRUCTURE(self, phrase):
        # No exception → passes the structure gate (it did NOT before the
        # narrowing — bare `\bplan\b` rejected every one of these).
        validate_mode_2_structure(phrase)

    @pytest.mark.parametrize("phrase", DUAL_USE_PASSING_SAMPLES["plan"])
    def test_PLAN_NOUN_PASSES_LEXICAL(self, phrase):
        # The noun was always lexically clean (plan lives on the structural
        # surface only); reasserted here so this file documents the full
        # "the noun is allowed" contract in one place.
        validate_mode_2_negative(phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "Your plan is to improve your worst-placed piece.",
            "The plan here revolves around the strong central pawn.",
            "Both sides have plans on opposite wings.",
            "A patient plan beats a rushed attack in this structure.",
        ],
    )
    def test_extra_noun_phrasings_pass_structure(self, phrase):
        validate_mode_2_structure(phrase)


# ---------------------------------------------------------------------------
# Pin 3 — the 'Plan:' header form is still rejected
# ---------------------------------------------------------------------------


class TestPlanHeaderStillRejected:
    """Narrowing the pattern must NOT regress the guard against the
    prescriptive ``Plan:`` section header — the move-suggestion shape."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "The position is favourable for White. Plan: trade pieces and convert.",
            "Plan: develop the pieces and seize the centre.",
            "Here is the plan: push the wing pawns and open the position.",
            "Plan : advance on the side where you are stronger.",
        ],
    )
    def test_PLAN_HEADER_STILL_REJECTED(self, phrase):
        with pytest.raises(AssertionError) as exc_info:
            validate_mode_2_structure(phrase)
        # The structure validator names the offending pattern; confirm it
        # was the plan-header pattern that fired (not some other section).
        assert "plan" in str(exc_info.value), (
            f"Header {phrase!r} was rejected but not by the plan pattern: "
            f"{exc_info.value}"
        )
