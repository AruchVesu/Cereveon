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
