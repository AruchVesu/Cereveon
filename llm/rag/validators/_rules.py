"""Shared rule data for the Mode-2 contract validators.

This module is the single source of truth for every forbidden / required
phrase set used by more than one Mode-2 validator function.  The four
public validator modules — ``llm.rag.contracts.validate_output``,
``llm.rag.validators.mode_2_negative``, ``llm.rag.validators.mode_2_structure``,
``llm.rag.validators.mode_2_semantic`` — re-export from here so the
Validator Coverage Matrix in ``docs/TESTING.md`` can cite one location
per rule instead of three.

Categories follow the matrix rows so a reviewer can trace each row
directly to a named constant:

  Row 1   ENGINE_LEXICAL_PHRASES   (substring; validate_output)
  Row 1   ENGINE_LEXICAL_PATTERNS  (regex form; mode_2_negative)
  Row 2   MOVE_ALGEBRAIC_PATTERNS  (mode_2_negative)
  Row 3   MOVE_ADVISORY_PATTERNS   (mode_2_structure)
  Row 4   SPECULATIVE_PATTERNS     (lexical, mode_2_negative)
  Row 4   SPECULATIVE_SEMANTIC     (semantic mirror, mode_2_semantic)
  Row 5   MATE_CLAIM_PATTERNS      (lexical reject, mode_2_negative)
  Row 5   MATE_INEVITABILITY_SEMANTIC  (require on ESV.type==mate)
  Row 6   MATE_INEVITABILITY_PHRASES   (validate_output, case_type=forced_mate)
  Row 7   MISSING_DATA_PHRASES         (validate_output, case_type=missing_data)
  Row 8   EQUAL_ADVANTAGE_WORDS    (mode_2_semantic, band==equal)
  Row 9   TACTICAL_NOUN_WORDS      (mode_2_semantic, tactical_flags==[])

The output firewall (Rows 10-14) lives in ``llm.rag.safety.output_firewall``
and is intentionally NOT migrated here: its concerns (PII / identity /
prompt-leak / bypass / harmful) are non-chess and its categories are
already named + bundled in that module.

Repair-loop keyword constants
-----------------------------
The bottom half of this module exports three bare-string tuples used
by ``llm.rag.llm.run_mode_2``'s repair loop:

  MATE_CLAIM_KEYWORDS    bare form of MATE_CLAIM_PATTERNS
  ADVISORY_KEYWORDS      advisory tokens that the repair loop strips
  STRUCTURAL_KEYWORDS    bare form of MOVE_ADVISORY_PATTERNS

Pre-2026-05-20, these lists were open-coded six times across
``run_mode_2.py``.  A retirement at the validator layer (PR #170
retired ``\\bshould\\b`` from SPECULATIVE_PATTERNS) did not propagate
to the repair loop, so the loop kept stripping the retired token
from LLM output — an over-rejection that defeated the validator
change.  Centralising the constants here closes that drift class.

DUAL_USE_TOKENS
---------------
``DUAL_USE_TOKENS`` is the registry of words whose lexical / semantic
treatment is *deliberately* lopsided — one surface accepts, the
other rejects, or a compound is forbidden while the bare word is
required.  Each entry names which surface enforces the token, the
PR that established the asymmetry, and the rationale.  When a future
contributor reaches for "just add the regex", they should consult
this registry first.

The underscore-prefixed module name marks this as the internal source of
truth: callsites and tests should import the public constants from the
validator modules, not from here.  Direct imports work but are not part
of the stable API.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Row 1 — Engine-mention vocabulary
# ---------------------------------------------------------------------------
# Two forms because two validators consume it differently:
#
#   ENGINE_LEXICAL_PHRASES — substring set, consumed by validate_output's
#     ``phrase in lower`` loop.  Matches mid-word ("engineer" contains
#     "engine") — accepted because validate_output is the broadest filter
#     and false-positive cost is low.
#
#   ENGINE_LEXICAL_PATTERNS — regex form with word boundaries, consumed
#     by validate_mode_2_negative.  Avoids the "engineer" false positive
#     for the lexical filter that fires on more text shapes.
ENGINE_LEXICAL_PHRASES: tuple[str, ...] = (
    "stockfish",
    "best move",
    "engine",
    "depth",
    "calculate",
    "variation",
)

ENGINE_LEXICAL_PATTERNS: tuple[str, ...] = (
    r"\bcalculate\b",
    r"\bcalculation\b",
    r"\bvariation\b",
    r"\bline\b",
)


# ---------------------------------------------------------------------------
# Row 2 — Algebraic move notation
# ---------------------------------------------------------------------------
MOVE_ALGEBRAIC_PATTERNS: tuple[str, ...] = (
    r"\b[KQRBN][a-h][1-8]\b",
    r"\b0-0(?:-0)?\b",
)


# ---------------------------------------------------------------------------
# Row 3 — Advisory / move-recommendation prose
# ---------------------------------------------------------------------------
MOVE_ADVISORY_PATTERNS: tuple[str, ...] = (
    r"\brecommended move\b",
    r"\bexample move\b",
    r"\bplan\b",
    r"\bwhite can\b",
    r"\bblack can\b",
    r"\bif it\b",
    r"\bconsider\b",
)


# ---------------------------------------------------------------------------
# Row 4 — Speculative / intent / hedging language
# ---------------------------------------------------------------------------
# The lexical surface (mode_2_negative) rejects on regex match against
# the full pattern set.  The semantic surface (mode_2_semantic) re-flags
# a tighter subset as substring matches when an ESV is in scope, so
# prompt-injected output that bypassed the lexical layer still fails the
# semantic gate.
SPECULATIVE_PATTERNS: tuple[str, ...] = (
    # ``\bshould\b`` was retired in PR #170 (2026-05-16).  It was the
    # single biggest reason DeepSeek Mode-2 output kept failing the
    # lexical gate — caught by PR #168's new exhaustion warning, which
    # showed every chat retry tripping ``Forbidden MODE-2 pattern
    # detected: pattern `\bshould\b```.  In coaching language ``should``
    # is overwhelmingly imperative ("you should develop your knight",
    # "White should castle quickly") rather than speculative; the
    # validator can't tell the two senses apart from a regex, so it
    # blocked legitimate coaching alongside actual speculation, leaving
    # the user with the repetitive deterministic fallback PR #169
    # later tightened.
    #
    # Speculative compounds and engine-voice forms remain caught:
    #   - ``should likely`` / ``should probably``    → \blikely\b / \bprobably\b
    #   - ``should consider``                        → \bconsider\b
    #   - ``the engine should …``                    → \bengine\b in SPECULATIVE_SEMANTIC
    #   - ``I think you should …``                   → \bI think\b
    #   - ``the engine wants …`` / ``plans to …``    → unchanged below
    r"\blikely\b",
    r"\bprobably\b",
    r"\bI think\b",
    r"\bthe engine wants\b",
    r"\bplans to\b",
    r"\bcarelessly\b",
    r"\black of planning\b",
    r"\bwith perfect play\b",
    r"\bactually winning\b",
    r"\bconsider\b",
)

SPECULATIVE_SEMANTIC: tuple[str, ...] = (
    "likely",
    "probably",
    "might",
    "engine",
    "wants to",
)


# ---------------------------------------------------------------------------
# Row 5 — Mate-claim lexical filter
# ---------------------------------------------------------------------------
# Forbid any output that names a mate without engine confirmation.  The
# semantic layer adds a separate REQUIRE check when the ESV does say
# mate (see MATE_INEVITABILITY_SEMANTIC below).
MATE_CLAIM_PATTERNS: tuple[str, ...] = (
    r"\bcheckmate\b",
    r"\bmate in \d+\b",
    r"\bforce(?:d)? mate\b",
    r"\bgame ends here\b",
)


# ---------------------------------------------------------------------------
# Rows 5 + 6 — Mate inevitability (two flavours)
# ---------------------------------------------------------------------------
# Row 6: validate_output REQUIRE check, gated on case_type=forced_mate.
#   Accepts the broader vocabulary ("unavoidable" is the adjective form
#   of "cannot be avoided").  Pinned by test_fake_llm.test_mate_softening.
MATE_INEVITABILITY_PHRASES: tuple[str, ...] = (
    "cannot be avoided",
    "inevitable",
    "unavoidable",
)

# Row 5: mode_2_semantic REQUIRE check, gated on ESV.evaluation.type=="mate".
#   Tighter accept set — semantic re-check is the ESV-scoped tighter gate
#   that defends against the lexical layer missing a mate frame entirely.
MATE_INEVITABILITY_SEMANTIC: tuple[str, ...] = (
    "inevitable",
    "forced",
)


# ---------------------------------------------------------------------------
# Row 7 — Missing-data refusal
# ---------------------------------------------------------------------------
MISSING_DATA_PHRASES: tuple[str, ...] = (
    "missing",
    "not enough information",
)


# ---------------------------------------------------------------------------
# Row 8 — Equal-band advantage vocabulary (semantic surface)
# ---------------------------------------------------------------------------
# When ESV.evaluation.band == "equal", output must NOT use advantage words.
EQUAL_ADVANTAGE_WORDS: tuple[str, ...] = (
    "slight advantage",
    "better",
    "winning",
    "initiative",
    "pressure",
)


# ---------------------------------------------------------------------------
# Row 9 — Invented tactical nouns (semantic surface)
# ---------------------------------------------------------------------------
# When ESV.tactical_flags == [], output must NOT name a tactical motif.
TACTICAL_NOUN_WORDS: tuple[str, ...] = (
    "fork",
    "pin",
    "sacrifice",
    "attack",
    "threat",
)


# ---------------------------------------------------------------------------
# Repair-loop keyword sets (consumed by ``llm.rag.llm.run_mode_2``)
# ---------------------------------------------------------------------------
# These are NOT validator rules — they are the bare-string keyword sets
# the repair loop uses to (a) classify which validator complaint a given
# error message refers to, and (b) sanitize candidate text before
# re-running validators.  Each set has a corresponding regex form above
# (or is derived from one); the bare-string form is what ``run_mode_2``
# needs to splice into its ``re.sub`` calls and ``any(k in p for k in
# ...)`` membership checks.
#
# Pinned by ``test_validator_taxonomy_invariants`` (drift guard).

# Bare form of MATE_CLAIM_PATTERNS, minus the ``\b...\b`` and the
# ``mate in \d+`` variable-length form (the repair loop handles digits
# separately in its mate-sanitization regex).
MATE_CLAIM_KEYWORDS: tuple[str, ...] = (
    "checkmate",
    "mate in",
    "forced mate",
)

# Advisory / prescriptive vocabulary the repair loop strips when the
# validator complained about advisory language.  ``should`` is
# DELIBERATELY ABSENT here — see DUAL_USE_TOKENS["should"] below for
# the rationale.  Pre-2026-05-20, this list contained ``should`` and
# the repair loop's aggressive sanitization replaced it with
# ``[REDACTED]``, which silently undid PR #170's intent (PR #170
# retired ``\bshould\b`` from SPECULATIVE_PATTERNS because it
# over-rejected imperative coaching, but ``run_mode_2`` kept stripping
# it from LLM output).
ADVISORY_KEYWORDS: tuple[str, ...] = (
    "must",
    "needs to",
    "best move",
)

# Bare form of MOVE_ADVISORY_PATTERNS (Row 3) without the ``\b...\b``
# wrappers.  Order matches MOVE_ADVISORY_PATTERNS so a side-by-side
# review is unambiguous.
STRUCTURAL_KEYWORDS: tuple[str, ...] = (
    "recommended move",
    "example move",
    "plan",
    "white can",
    "black can",
    "if it",
    "consider",
)


# ---------------------------------------------------------------------------
# Dual-use token registry
# ---------------------------------------------------------------------------
# Words whose lexical / semantic treatment is *deliberately* asymmetric.
# Future contributors who reach for "just add a regex" for one of these
# words should read the entry first.
#
# Each entry:
#   enforced_at  — which surface enforces the token, or "none" if it
#                  was retired and is now accept-only at every surface
#   rationale    — why the asymmetry exists, in plain prose
#   pr           — the PR that established the current state (or None
#                  if the asymmetry predates the registry)
#   date         — the date the asymmetry was established (yyyy-mm-dd)
#
# Pinned by ``test_validator_taxonomy_invariants`` — every entry must
# have a passing-sample row in ``llm/rag/validators/_fixtures.py`` so
# adding a token without a regression sample is loud at CI time.
DUAL_USE_TOKENS: dict[str, dict[str, object]] = {
    "should": {
        "enforced_at": "none",
        "rationale": (
            "Imperative in coaching prose ('you should develop your knight'); "
            "regex cannot distinguish from speculative ('White should likely "
            "convert').  Speculative compounds remain caught via the other "
            "patterns that match their hedging marker: 'should likely' → "
            "\\blikely\\b, 'should consider' → \\bconsider\\b, 'I think you "
            "should' → \\bI think\\b."
        ),
        "pr": 170,
        "date": "2026-05-16",
    },
    "consider": {
        "enforced_at": "lexical+structural",
        "rationale": (
            "Dual-use: coaching directive ('consider the open file') vs "
            "advisory section header ('Consider trading queens:').  Enforced "
            "at BOTH surfaces deliberately — the structural match catches the "
            "section-header form (MOVE_ADVISORY_PATTERNS row 3); the lexical "
            "match catches inline advisory phrasing (SPECULATIVE_PATTERNS "
            "row 4).  An LLM that drops both surfaces' triggers has phrased "
            "its coaching without prescriptive register."
        ),
        "pr": None,
        "date": None,
    },
    "plan": {
        "enforced_at": "structural",
        "rationale": (
            "Strategic noun ('White's plan involves piece activity') vs "
            "advisory section header ('Plan: trade pieces and convert').  "
            "Enforced at the structural surface only; a bare \\bplan\\b "
            "in SPECULATIVE_PATTERNS would over-reject the strategic-noun "
            "form.  The structural surface's MOVE_ADVISORY_PATTERNS row 3 "
            "catches the header form, which is the actually-forbidden shape."
        ),
        "pr": None,
        "date": None,
    },
    "forced": {
        "enforced_at": "semantic-required",
        "rationale": (
            "Required by MATE_INEVITABILITY_SEMANTIC when "
            "ESV.evaluation.type == 'mate'.  Distinct from the lexical "
            "MATE_CLAIM_PATTERNS forbidden bigram '\\bforce(?:d)? mate\\b' — "
            "bare 'forced' is accept-only; the bigram 'forced mate' is "
            "the forbidden compound.  PR #167 (2026-05-15) traced the "
            "lockout: the deterministic fallback wrote 'this is a forced "
            "mate' thinking the bare 'forced' satisfied the semantic "
            "require, not realising the bigram tripped the lexical reject.  "
            "Pinned accept-carrier: 'Mate is inevitable' (uses the safer "
            "MATE_INEVITABILITY_PHRASES vocabulary)."
        ),
        "pr": 167,
        "date": "2026-05-15",
    },
}
