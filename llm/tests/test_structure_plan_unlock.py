"""
Regression tests for fully retiring ``plan`` from the Mode-2 structural
filter ``MOVE_ADVISORY_PATTERNS`` (2026-06-07).

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

History: bare ``\\bplan\\b`` was first narrowed to the advisory-header
form ``\\bplan\\b\\s*:`` on 2026-06-04 (keep the "Plan:" heading
rejected, accept the noun).  The 2026-06-07 real-model diagnostic showed
DeepSeek routinely writes a "Plan:" heading and the heading word itself
is harmless — any move-content under it is still caught by
MOVE_ALGEBRAIC_PATTERNS / "white can" / "black can" — so the header form
was pure over-rejection too.  ``plan`` is now FULLY retired from the
structural surface: NO surface enforces it.

Pinned invariants
-----------------
 1. PLAN_FULLY_RETIRED_FROM_MOVE_ADVISORY — neither bare ``\\bplan\\b``
                                     NOR the colon-anchored
                                     ``\\bplan\\b\\s*:`` is in
                                     MOVE_ADVISORY_PATTERNS.
 2. PLAN_NOUN_PASSES_STRUCTURE     — strategic-noun "plan" sentences
                                     pass validate_mode_2_structure
                                     (and the lexical gate).
 3. PLAN_HEADER_NOW_PASSES         — a "Plan:" heading now PASSES
                                     validate_mode_2_structure (the
                                     header word is harmless on its own).
 4. PLAN_REMAINS_DOCUMENTED        — DUAL_USE_TOKENS keeps the rationale
                                     with enforced_at == "none".
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


class TestPlanFullyRetiredFromMoveAdvisory:
    """Neither the bare ``\\bplan\\b`` regex NOR the colon-anchored
    header form ``\\bplan\\b\\s*:`` may be in MOVE_ADVISORY_PATTERNS.
    Re-adding either re-opens the over-rejection that forced the
    templated deterministic fallback."""

    def test_PLAN_BARE_NOT_IN_MOVE_ADVISORY(self):
        assert r"\bplan\b" not in MOVE_ADVISORY_PATTERNS, (
            "Bare `\\bplan\\b` was re-added to MOVE_ADVISORY_PATTERNS.  It "
            "over-rejects the strategic noun ('your plan is to ...') that "
            "the Mode-2 system prompt explicitly invites, dropping every "
            "plan-mentioning reply to the templated fallback.  'plan' is "
            "fully retired — see test_PLAN_HEADER_NOW_PASSES for the "
            "inverse pin."
        )

    def test_PLAN_HEADER_FORM_ALSO_GONE(self):
        """The colon-anchored header form ``\\bplan\\b\\s*:`` (the
        2026-06-04 narrowing) was fully retired 2026-06-07 — the 'Plan:'
        heading word is harmless on its own because move-content under it
        is still caught by notation / 'white can' / 'black can'."""
        assert r"\bplan\b\s*:" not in MOVE_ADVISORY_PATTERNS, (
            "The header form `\\bplan\\b\\s*:` was re-added to "
            "MOVE_ADVISORY_PATTERNS.  It was fully retired 2026-06-07 — "
            "DeepSeek routinely writes a 'Plan:' heading and the heading "
            "word itself is harmless.  Keep 'plan' accept-only at every "
            "surface (see DUAL_USE_TOKENS['plan'])."
        )
        # FORBIDDEN_SECTIONS is the structure validator's view of the
        # same list — keep them in lock-step.
        assert r"\bplan\b\s*:" not in FORBIDDEN_SECTIONS
        assert r"\bplan\b" not in FORBIDDEN_SECTIONS

    def test_plan_remains_documented_as_dual_use(self):
        """DUAL_USE_TOKENS MUST keep documenting why "plan" is treated
        asymmetrically.  Dropping the entry loses the rationale the next
        contributor needs before touching the pattern."""
        assert "plan" in DUAL_USE_TOKENS, (
            "`plan` is missing from DUAL_USE_TOKENS in _rules.py.  Keep "
            "the registry entry — it records the bare→header→fully-retired "
            "history and why the word is accept-only now."
        )
        assert DUAL_USE_TOKENS["plan"]["enforced_at"] == "none", (
            "DUAL_USE_TOKENS['plan'] must record enforced_at='none' — "
            "'plan' was fully retired from every surface 2026-06-07.  If "
            "you are re-introducing a 'plan' regex at any surface, also "
            "update this registry entry with the new surface name."
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
# Pin 3 — the 'Plan:' header form now PASSES (fully retired 2026-06-07)
# ---------------------------------------------------------------------------


class TestPlanHeaderNowPasses:
    """The 2026-06-07 full retirement means a prescriptive ``Plan:``
    section header no longer trips ``validate_mode_2_structure`` — the
    heading word is harmless because move-content under it is still
    caught by notation / 'white can' / 'black can'.  Samples are kept
    free of those still-active triggers so the structural surface is
    unambiguously what's under test."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "The position is favourable for White. Plan: trade pieces and convert.",
            "Plan: develop the pieces and seize the centre.",
            "Here is the plan: push the wing pawns and open the position.",
            "Plan : advance on the side where you are stronger.",
        ],
    )
    def test_PLAN_HEADER_NOW_PASSES(self, phrase):
        # No exception → the 'Plan:' header passes the structure gate
        # (it did NOT before the 2026-06-07 full retirement).
        validate_mode_2_structure(phrase)
