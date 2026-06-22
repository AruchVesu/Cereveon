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

# ``\bline\b`` retired 2026-06-07: over-broad — it caught ordinary coaching
# prose ("open lines toward the king", "hold the line", "the long diagonal
# line") far more often than the engine-analysis sense ("the main line is
# ..."), which is already covered by ``\bvariation\b`` + ``\bcalculate\b``.
# Real-model diagnostic showed it vetoing legitimate king-safety answers.
#
# ``\bstockfish\b`` / ``\bbest move\b`` / ``\bdepth\s+\d+\b`` added 2026-06-22.
# All three derive from ENGINE_LEXICAL_PHRASES above but were ONLY enforced
# by ``validate_output`` — which has no live caller (it runs in run_mode_2's
# /explain test surface and study_plan/verdict).  The live /chat,
# /chat/stream, and /live/move boundary runs ``validate_mode_2_negative``
# (this list) but NOT ``validate_output``, so an LLM reply naming
# "Stockfish", "best move", or "at depth 18" reached the user — violating the
# "no engine mentions" invariant (ARCHITECTURE.md; THREAT_MODEL.md T1
# defence-in-depth layer).  This closes that live-path gap.
#
# Two deliberate departures from a literal mirror of the PHRASES set:
#   * ``depth`` is matched here only in its engine-analysis sense
#     ``\bdepth\s+\d+\b`` ("at depth 18", "depth 20"), NOT the bare word.
#     Bare ``\bdepth\b`` would veto ordinary coaching prose ("look at this in
#     depth", "in-depth understanding") and drop the user to the
#     deterministic fallback — the over-rejection class this project has
#     repeatedly retired (cf. the ``\bline\b`` / ``\bshould\b`` history
#     above).  ``validate_output`` keeps the broader bare-substring "depth"
#     on the /explain surface, where latency is looser and the cost of a
#     false positive is lower.
#   * ``engine`` is NOT mirrored at all: it is already a live FORBID via the
#     semantic gate (SPECULATIVE_SEMANTIC), and adding it here would only
#     change which validator fires first on engine-only output (corpus
#     exception-type churn) for no added coverage.
ENGINE_LEXICAL_PATTERNS: tuple[str, ...] = (
    r"\bcalculate\b",
    r"\bcalculation\b",
    r"\bvariation\b",
    r"\bstockfish\b",
    r"\bbest move\b",
    r"\bdepth\s+\d+\b",
)


# ---------------------------------------------------------------------------
# Row 2 — Algebraic move notation
# ---------------------------------------------------------------------------
# The piece letter is OPTIONAL (``[KQRBN]?``) so PAWN moves — a bare
# file+rank like "e4" / "d5" with no piece prefix — are caught, not just
# piece moves ("Nf3").  Pre-2026-06-05 the mandatory ``[KQRBN]`` let pawn
# squares and letter-O castling slip past this boundary entirely (the
# only notation gate on the live /chat + /live/move path), so an LLM
# reply like "your plan is to play e4" leaked notation to the user.  This
# now matches the project's canonical notation regex in
# ``llm.rag.validators.sanitize.NOTATION_REGEX`` (``[BNRQK]?[a-h][1-8]``).
# Castling has two forms: digit-zero ``0-0`` and letter-O ``O-O`` — both
# are listed (the digit form is kept verbatim so the architectural-
# invariant guard ``"0-0" in p`` in test_architectural_invariants.py
# still finds a castling pattern).  ``re.IGNORECASE`` on the negative
# validator makes both case-insensitive.
MOVE_ALGEBRAIC_PATTERNS: tuple[str, ...] = (
    r"\b[KQRBN]?[a-h][1-8]\b",
    r"\b0-0(?:-0)?\b",
    r"\bO-O(?:-O)?\b",
)


# ---------------------------------------------------------------------------
# Row 3 — Advisory / move-recommendation prose
# ---------------------------------------------------------------------------
# Retired 2026-06-07 (real-model diagnostic — only 2/8 natural coaching
# answers survived the validator stack):
#   - ``\bplan\b\s*:``  — DeepSeek routinely writes a "Plan:" heading; the
#     heading itself is harmless (any move-content under it is still caught
#     by notation / "white can" / "black can"), so the header word was pure
#     over-rejection.  (It had already been narrowed from bare ``\bplan\b``
#     on 2026-06-04; now fully retired.)
#   - ``\bif it\b``     — matched the everyday phrase "bad if it leaves your
#     king ..." far more than any advisory construct.
#   - ``\bconsider\b``  — normal coaching directive ("consider castling");
#     also retired from SPECULATIVE_PATTERNS below.
# What remains is genuine move-recommendation prose; concrete moves are
# still blocked by MOVE_ALGEBRAIC_PATTERNS.
MOVE_ADVISORY_PATTERNS: tuple[str, ...] = (
    r"\brecommended move\b",
    r"\bexample move\b",
    r"\bwhite can\b",
    r"\bblack can\b",
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
    #
    # ``\blikely\b``, ``\bprobably\b``, ``\bconsider\b`` retired 2026-06-07
    # (real-model diagnostic): "likely"/"probably" are ordinary hedged
    # coaching ("this would likely weaken your king"), and "consider" is a
    # normal directive ("consider castling") — conditional teaching grounded
    # in the ESV, not engine-fact invention.  The clear-overreach / engine-
    # voice forms remain ("I think", "the engine wants", "plans to", "with
    # perfect play", "actually winning"), and the hard guards (no notation,
    # no engine words, no mate misframing, no advantage-on-equal, no invented
    # motifs) are untouched.
    r"\bI think\b",
    r"\bthe engine wants\b",
    r"\bplans to\b",
    r"\bcarelessly\b",
    r"\black of planning\b",
    r"\bwith perfect play\b",
    r"\bactually winning\b",
)

# ``likely`` / ``probably`` / ``might`` retired here (2026-06-07) — hedged
# coaching language, not engine-fact speculation.  ``wants to`` retired too:
# it caught ordinary coaching ("your opponent wants to open the centre") far
# more than the engine-voice sense.  Only the literal engine-leak word
# ``engine`` remains (whole-word; also caught by validate_output).
SPECULATIVE_SEMANTIC: tuple[str, ...] = (
    "engine",
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
#
# ``initiative`` and ``pressure`` were retired 2026-06-06: they are
# general strategic vocabulary, not direct advantage claims, and a
# substring match over-rejected legitimate coaching ("pressure on the
# backward pawn", "seize the initiative on the wing", and especially
# king-safety answers like "the opponent builds pressure against your
# king").  On an equal-band quiet position this silently vetoed almost
# every Mode-2 LLM reply and dropped the user to the templated
# deterministic fallback (the "nothing changed" report).
#
# ``better`` retired 2026-06-07: too common a comparative ("a better
# square", "better to castle first", "better piece coordination") to
# distinguish from the advantage claim "White is better" even with word
# boundaries.  The retained ``slight advantage`` / ``winning`` are the
# direct who-stands-better claims that contradict an "equal" band.
# Matched WHOLE-WORD by mode_2_semantic (not substring).  Pinned by
# test_semantic_strategic_vocab_unlock.py.
EQUAL_ADVANTAGE_WORDS: tuple[str, ...] = (
    "slight advantage",
    "winning",
)


# ---------------------------------------------------------------------------
# Row 9 — Invented tactical nouns (semantic surface)
# ---------------------------------------------------------------------------
# When ESV.tactical_flags == [], output must NOT name a tactical motif.
#
# ``attack`` and ``threat`` were retired 2026-06-06: unlike fork / pin /
# sacrifice they are NOT concrete tactical motifs — they are general
# strategic words ("attack the weak pawn", "threats against the king",
# "build an attack", "the square is under threat").  Naming a fork/pin/
# sacrifice when the engine reports no tactical flags invents a concrete
# tactic; saying "attack"/"threat" does not.  The substring match
# over-rejected normal coaching — most visibly king-safety answers, which
# REQUIRE this vocabulary — vetoing the LLM reply on a quiet position and
# forcing the templated deterministic fallback.  The retained motifs keep
# the anti-hallucination guarantee that matters.  Pinned by
# test_semantic_strategic_vocab_unlock.py.
TACTICAL_NOUN_WORDS: tuple[str, ...] = (
    "fork",
    "pin",
    "sacrifice",
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
# review is unambiguous.  These are NOT matched against LLM output — they
# are substring-matched against the *error's regex string* in
# ``run_mode_2`` to CLASSIFY which validator complained.
#
# "plan", "if it", "consider" were dropped 2026-06-07 in lockstep with
# their retirement from MOVE_ADVISORY_PATTERNS (keep this list == the bare
# form of that list, or the taxonomy/repair-classification drifts).
STRUCTURAL_KEYWORDS: tuple[str, ...] = (
    "recommended move",
    "example move",
    "white can",
    "black can",
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
        "enforced_at": "none",
        "rationale": (
            "Retired 2026-06-07 from BOTH surfaces (was lexical+structural). "
            "A normal coaching directive ('consider the open file', 'consider "
            "castling') — the substring match over-rejected it constantly in "
            "real DeepSeek output (king-safety / plan questions).  No surface "
            "enforces it now; the system prompt still discourages a "
            "prescriptive register.  Same retirement shape as ``\\bshould\\b`` "
            "(PR #170)."
        ),
        "pr": None,
        "date": "2026-06-07",
    },
    "plan": {
        "enforced_at": "none",
        "rationale": (
            "Retired 2026-06-07.  History: bare ``\\bplan\\b`` over-rejected "
            "the strategic noun → narrowed to the header form "
            "``\\bplan\\b\\s*:`` (2026-06-04) → fully retired now.  DeepSeek "
            "routinely writes a 'Plan:' heading; the heading word itself is "
            "harmless because any move-content under it is still caught by "
            "MOVE_ALGEBRAIC_PATTERNS / 'white can' / 'black can'.  No surface "
            "enforces 'plan' now."
        ),
        "pr": None,
        "date": "2026-06-07",
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
