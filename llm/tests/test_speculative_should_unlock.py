"""
Regression tests for PR #170 — retiring ``\\bshould\\b`` from the
Mode-2 lexical filter ``SPECULATIVE_PATTERNS``.

Context
-------
PR #168 added a WARNING log when the chat retry loop exhausted due to
repeated validator rejection.  The first live-game test after PR #168
deployed (2026-05-16) showed the SMOKING-GUN warning:

    Mode-2 LLM failed after 3 attempts
      (AssertionError: Forbidden MODE-2 pattern detected:
       pattern `\\bshould\\b`); using deterministic fallback

DeepSeek's chat output kept using ``"should"`` — one of the most
natural coaching words in English — and the lexical filter vetoed every
retry, dropping the user back to the deterministic fallback.  In
coaching prose ``"should"`` is overwhelmingly imperative (``"you should
develop your knight"``, ``"White should castle quickly"``) rather than
speculative; the regex can't distinguish the two, so blocking the bare
word permanently rejected legitimate coaching alongside actual
speculation.

PR #170 retired ``\\bshould\\b`` from SPECULATIVE_PATTERNS.  A later
relaxation (2026-06-07) retired the hedging markers ``\\blikely\\b`` /
``\\bprobably\\b`` / ``\\bconsider\\b`` too (ordinary hedged coaching /
normal directives — see _rules.SPECULATIVE_PATTERNS), so the
``should``-compounds those markers used to catch now PASS as well.  The
clear-overreach / engine-voice forms remain the load-bearing speculative
guards:

  - ``should likely`` / ``should probably`` → NOW PASS (``\\blikely\\b``
                                              / ``\\bprobably\\b`` retired
                                              2026-06-07).
  - ``should consider``                     → NOW PASSES (``\\bconsider\\b``
                                              retired 2026-06-07 from both
                                              SPECULATIVE_PATTERNS and
                                              MOVE_ADVISORY_PATTERNS).
  - ``the engine should …``                 → caught by ``"engine"`` in
                                              SPECULATIVE_SEMANTIC.
  - ``I think you should …``                → caught by ``\\bI think\\b``.

Pinned invariants
-----------------
 1. SHOULD_NOT_IN_SPECULATIVE_PATTERNS — the ``\\bshould\\b`` regex is
                                          not in SPECULATIVE_PATTERNS.
 2. SHOULD_BARE_PASSES                  — a coaching sentence using
                                          bare ``should`` passes
                                          ``validate_mode_2_negative``.
 3. SHOULD_LIKELY_NOW_PASSES            — ``"should likely"`` now passes
                                          (``\\blikely\\b`` retired
                                          2026-06-07).
 4. SHOULD_PROBABLY_NOW_PASSES          — ``"should probably"`` now
                                          passes (``\\bprobably\\b``
                                          retired 2026-06-07).
 5. SHOULD_CONSIDER_NOW_PASSES          — ``"should consider"`` now
                                          passes (``\\bconsider\\b``
                                          retired 2026-06-07).
 6. I_THINK_YOU_SHOULD_STILL_REJECTED   — ``"I think you should …"``
                                          still fails via ``\\bI think\\b``.
"""

from __future__ import annotations

import pytest

from llm.rag.validators._fixtures import DUAL_USE_PASSING_SAMPLES
from llm.rag.validators._rules import DUAL_USE_TOKENS, SPECULATIVE_PATTERNS
from llm.rag.validators.mode_2_negative import validate_mode_2_negative


# ---------------------------------------------------------------------------
# Pin 1 — the regex is gone from the list
# ---------------------------------------------------------------------------


class TestShouldNotInSpeculativePatterns:
    """The bare-``\\bshould\\b`` regex MUST NOT be in SPECULATIVE_PATTERNS.
    Future contributors re-adding it should fail this test loudly."""

    def test_SHOULD_NOT_IN_SPECULATIVE_PATTERNS(self):
        assert r"\bshould\b" not in SPECULATIVE_PATTERNS, (
            "Bare `\\bshould\\b` was re-added to SPECULATIVE_PATTERNS.  "
            "PR #170 retired it because it over-blocked imperative "
            "coaching language ('you should develop your knight').  "
            "Speculative compounds (`should likely`, `should consider`, "
            "`I think you should`) are caught by other patterns that "
            "remain — see test_speculative_compounds_still_rejected "
            "below for the inverse pin."
        )

    def test_should_remains_documented_as_dual_use(self):
        """The DUAL_USE_TOKENS registry MUST keep documenting why
        ``should`` is accept-only.  If a future contributor removes
        the registry entry, the rationale + PR reference disappear and
        the next retirement candidate starts from zero context."""
        assert "should" in DUAL_USE_TOKENS, (
            "`should` is missing from DUAL_USE_TOKENS in _rules.py.  "
            "Keep the registry entry alive — it carries the PR #170 "
            "rationale that future contributors need before adding any "
            "speculative-token regex."
        )
        entry = DUAL_USE_TOKENS["should"]
        assert entry["enforced_at"] == "none", (
            "DUAL_USE_TOKENS['should'] must record enforced_at='none' — "
            "PR #170 retired the token from every surface.  If you are "
            "re-introducing a `should` regex at any surface, also update "
            "this registry entry with the new surface name."
        )


# ---------------------------------------------------------------------------
# Pin 2 — bare 'should' coaching passes
# ---------------------------------------------------------------------------


class TestBareShouldCoachingAccepted:
    """Coaching sentences using bare ``should`` (imperative form) must
    pass ``validate_mode_2_negative`` — this is the high-bar pin that
    re-introducing ``\\bshould\\b`` would break.

    Samples now sourced from
    ``llm.rag.validators._fixtures.DUAL_USE_PASSING_SAMPLES['should']``
    so a future retirement test can reuse the canonical list rather
    than re-author it."""

    @pytest.mark.parametrize("phrase", DUAL_USE_PASSING_SAMPLES["should"])
    def test_SHOULD_BARE_PASSES(self, phrase):
        # No exception → passes the negative gate.
        validate_mode_2_negative(phrase)


# ---------------------------------------------------------------------------
# Pin 3-6 — speculative compounds & engine-voice still rejected
# ---------------------------------------------------------------------------


class TestSpeculativeCompoundsRelaxation:
    """The 2026-06-07 relaxation retired the hedging markers
    (``\\blikely\\b`` / ``\\bprobably\\b`` / ``\\bconsider\\b``) that
    used to catch the ``should``-compounds, so those compounds now PASS
    ``validate_mode_2_negative``.  The clear-overreach / engine-voice
    forms remain rejected (see ``test_I_THINK_YOU_SHOULD_STILL_REJECTED``
    below) — that's the line the relaxation deliberately did NOT cross."""

    def test_SHOULD_LIKELY_NOW_PASSES(self):
        """``should likely`` now PASSES — ``\\blikely\\b`` retired
        2026-06-07 (ordinary hedged coaching, e.g. "this would likely
        weaken your king")."""
        validate_mode_2_negative("White should likely convert this edge.")

    def test_SHOULD_PROBABLY_NOW_PASSES(self):
        """``should probably`` now PASSES — ``\\bprobably\\b`` retired
        2026-06-07."""
        validate_mode_2_negative("Black should probably trade queens.")

    def test_SHOULD_CONSIDER_NOW_PASSES(self):
        """``should consider`` now PASSES — ``\\bconsider\\b`` retired
        2026-06-07 from both SPECULATIVE_PATTERNS and
        MOVE_ADVISORY_PATTERNS ("consider castling" is a normal
        directive)."""
        validate_mode_2_negative("You should consider the open file.")

    def test_I_THINK_YOU_SHOULD_STILL_REJECTED(self):
        """``I think you should`` → still caught by ``\\bI think\\b``
        (LLM-voice).  The relaxation retired the hedging markers but kept
        the explicit first-person speculation guard."""
        with pytest.raises(AssertionError, match=r"\\bI think\\b"):
            validate_mode_2_negative("I think you should castle the king.")
