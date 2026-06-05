"""Shared test fixtures for the Mode-2 validator surface.

This module exists so that two kinds of validator tests can pull from
one source of truth instead of re-authoring sample sentences per file:

  Pattern-coverage tests
      ``llm/rag/tests/unit/test_validator_pattern_coverage.py`` —
      anti-mutation pins that walk each forbidden / required list and
      assert one specific carrier sentence triggers / satisfies it.

  Bidirectional unlock tests
      e.g. ``llm/tests/test_speculative_should_unlock.py`` — pin (a)
      a retired pattern is *gone* from the live list, AND (b) a set
      of coaching sentences using the retired token still pass.  The
      memory ``feedback-mode-2-validator-overrejection`` names this
      as the template for retiring a pattern.

Pre-2026-05-20, each unlock test re-authored its own list of
"sentences this token must not block".  When a future retirement
runs, fresh samples get invented every time; meanwhile the canonical
"what does this regex catch" map (``NEG_SAMPLES`` in the pattern-
coverage file) is opaque to outside callers.

Centralising the sample data here means: one place to add a new
sample, one place to read the canonical positive / negative carrier
for any token.

What is exported
----------------
``DUAL_USE_PASSING_SAMPLES``
    Maps each dual-use token (from
    ``llm.rag.validators._rules.DUAL_USE_TOKENS``) to a list of
    coaching sentences that MUST pass ``validate_mode_2_negative``.
    Pinned by ``test_validator_taxonomy_invariants``.

``ACCEPT_CARRIERS``
    The "PR #167 lesson" registry — for each required semantic
    phrase, the carrier sentence the deterministic fallback uses to
    satisfy the requirement.  Each carrier must pass the lexical
    surface (this is the cross-surface contradiction guard).

``NEG_PATTERN_SAMPLES``
    A view over ``test_validator_pattern_coverage.py``'s in-file
    ``NEG_SAMPLES`` list, exported here so downstream tests can look
    up "the canonical positive sample for pattern X" without re-
    authoring it.  Construction is deliberately a literal list (not
    a derivation from ``_rules.py``) — the pattern-coverage test's
    completeness guard catches additions/removals at CI time.

This module is internal — like ``_rules.py``, the leading underscore
marks the module as not part of the stable public surface.  Tests
may import freely; production code should not.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# DUAL_USE_PASSING_SAMPLES
# ---------------------------------------------------------------------------
# Sentences that MUST pass ``validate_mode_2_negative`` despite
# containing the listed token.  Every entry in
# ``_rules.DUAL_USE_TOKENS`` must have a row here — pinned by
# ``test_validator_taxonomy_invariants``.
#
# Adding a new dual-use token: add a row in BOTH places in the same
# commit.  Retiring a token: remove from BOTH places + add a
# regression test that the historic samples still pass.
DUAL_USE_PASSING_SAMPLES: dict[str, list[str]] = {
    "should": [
        "You should develop your pieces toward the centre.",
        "White should castle quickly to safeguard the king.",
        "Black should activate the rook on the open file.",
        "You should look at piece safety before committing.",
        # Bare "should" embedded in a fuller coaching paragraph.
        (
            "The position is roughly equal in the opening. "
            "You should focus on completing your development "
            "before launching any pawn push."
        ),
    ],
    "consider": [
        # ``consider`` is enforced at BOTH surfaces (lexical +
        # structural) — the dual-use note in _rules.DUAL_USE_TOKENS
        # captures the asymmetry but the SAMPLE here exists for the
        # taxonomy-invariant test that expects every entry to have a
        # row.  Coaching contexts that legitimately use the word
        # without tripping the lexical filter are rare; rely on the
        # paraphrase instead ("think about", "look at", "examine").
        # The sample below uses the substring inside a different
        # word ("consideration") to demonstrate that the word-
        # boundary regex ``\bconsider\b`` does NOT match.
        "Sound positional play involves careful consideration of pawn breaks.",
    ],
    "plan": [
        # ``plan`` is enforced ONLY at the structural surface, and since
        # the 2026-06-04 narrowing only in the ``Plan:`` header form
        # (``\bplan\b\s*:``) — the bare ``\bplan\b`` over-rejected the
        # strategic noun.  These strategic-noun sentences now pass BOTH
        # the lexical filter AND the structural filter (see
        # test_structure_plan_unlock.py for the structural-surface pin).
        "The structure favours a long-term plan based on piece activity.",
        # NB: keep these free of square notation ("d4", "e5", ...) — the
        # negative gate's MOVE_ALGEBRAIC_PATTERNS catches bare pawn squares
        # (broadened 2026-06-05), and these samples must isolate the *word*
        # "plan", not smuggle in a separate notation violation.
        "White's plan involves preparing a central pawn break.",
        "A clear positional plan beats a vague tactical try.",
    ],
    "forced": [
        # ``forced`` is REQUIRED by the semantic surface when
        # ESV.evaluation.type == "mate".  The lexical filter forbids
        # the compound ``forced mate`` (MATE_CLAIM_PATTERNS) but
        # accepts bare ``forced``.  These sentences satisfy the
        # semantic require check WITHOUT tripping the lexical
        # forbidden compound — the safer pattern is to phrase the
        # mate as "inevitable" (see ACCEPT_CARRIERS below) but bare
        # "forced" remains a valid accept-token.
        "The outcome is forced and decisive.",
        "Resignation here is forced by the material loss.",
    ],
}


# ---------------------------------------------------------------------------
# ACCEPT_CARRIERS
# ---------------------------------------------------------------------------
# Cross-surface contradiction guard registry.  For each required
# substring the semantic gates accept, this records the carrier
# sentence the deterministic fallback uses to satisfy that gate.
#
# Every carrier sentence MUST pass ``validate_mode_2_negative`` — the
# PR #167 (2026-05-15) regression was a fallback that emitted "This
# is a forced mate" thinking ``forced`` was enough to satisfy
# MATE_INEVITABILITY_SEMANTIC, not realising the bigram tripped the
# lexical MATE_CLAIM_PATTERNS reject.  Pinned by
# ``test_validator_taxonomy_invariants::test_accept_carriers_pass_lexical``.
#
# Each entry:
#   required_phrase   — the substring that satisfies a semantic
#                       require check (from
#                       _rules.MATE_INEVITABILITY_PHRASES,
#                       MATE_INEVITABILITY_SEMANTIC, or
#                       MISSING_DATA_PHRASES)
#   carrier           — the sentence the deterministic fallback
#                       emits.  Must contain the required substring
#                       and must pass the lexical filter.
#   where             — pointer to the fallback that uses this
#                       carrier (for grep-from-failing-test).
ACCEPT_CARRIERS: list[tuple[str, str, str]] = [
    (
        "inevitable",
        "Mate is inevitable.",
        "preferred mate-decisiveness phrasing (PR #167 fix)",
    ),
    (
        "cannot be avoided",
        "Mate cannot be avoided in this position.",
        "validate_output forced_mate REQUIRED_ON_MATE alternative",
    ),
    (
        "unavoidable",
        "Mate is unavoidable here.",
        "validate_output forced_mate REQUIRED_ON_MATE alternative",
    ),
    (
        "forced",
        "The outcome is forced and decisive.",
        "MATE_INEVITABILITY_SEMANTIC require — bare 'forced' "
        "without the lexically-forbidden 'forced mate' bigram",
    ),
    (
        "missing",
        "Required information is missing for this position.",
        "validate_output missing_data REQUIRED_ON_MISSING",
    ),
    (
        "not enough information",
        "There is not enough information to evaluate this position.",
        "validate_output missing_data REQUIRED_ON_MISSING alternative",
    ),
]
