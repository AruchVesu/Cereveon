"""
Cross-surface invariants for the Mode-2 validator taxonomy.

This file complements ``test_validator_pattern_coverage.py`` (which
walks each individual forbidden / required list and asserts the
literal pattern catches its trigger).  This file pins the
*relationships* between surfaces — the class of bugs that can only
be caught by looking at lexical, structural, and semantic gates
together:

  1. **Accept-carrier sentences pass the lexical surface.**
     PR #167 (2026-05-15) regressed because the deterministic
     fallback emitted "this is a forced mate" thinking the bare
     ``forced`` satisfied MATE_INEVITABILITY_SEMANTIC, not realising
     the bigram tripped MATE_CLAIM_PATTERNS' lexical reject.  This
     gate iterates the ``ACCEPT_CARRIERS`` registry in
     ``_fixtures.py`` and asserts each carrier passes lexical.

  2. **Dual-use tokens have passing-sample coverage.**
     ``DUAL_USE_TOKENS`` in ``_rules.py`` documents why each
     dual-use word is treated asymmetrically.  Every entry must have
     a corresponding row in ``DUAL_USE_PASSING_SAMPLES`` proving the
     surface where it's accepted actually accepts it.  Missing rows
     are missing regression coverage; the registry entry without a
     test pin is documentation-only.

  3. **Repair-loop keyword sets match the published taxonomy.**
     The ``run_mode_2`` repair loop consumes ``ADVISORY_KEYWORDS``,
     ``MATE_CLAIM_KEYWORDS``, ``STRUCTURAL_KEYWORDS`` from
     ``_rules.py``.  This drift guard asserts the imported values
     and their derivative regex objects in ``run_mode_2`` reflect
     the published taxonomy — catches a future "I'll just add the
     string here" mistake.

  4. **No accidental cross-surface duplication.**
     ``\\bplan\\b`` and ``\\bconsider\\b`` are dual-use enforced
     deliberately at one or both specific surfaces (per
     ``DUAL_USE_TOKENS``).  No OTHER lexical token should appear in
     more than one ``*_PATTERNS`` set — overlap means retiring it
     from one surface would silently leave it in the other.  This
     test snapshots the overlap set and asserts it matches the
     documented dual-use registry.

Stable test IDs (do NOT rename):
  TXI_ACC_*   ACCEPT_CARRIERS pass lexical
  TXI_DUR_*   DUAL_USE_TOKENS coverage
  TXI_SAM_*   DUAL_USE_PASSING_SAMPLES pass their advertised surface
  TXI_RM2_*   run_mode_2 keyword-import drift
  TXI_OVL_*   inter-list overlap matches the documented registry
"""

from __future__ import annotations

import re

import pytest

from llm.rag.llm.run_mode_2 import (
    _ADVISORY_RE,
    _ENGINE_PHRASES_FOR_STRIP,
)
from llm.rag.llm.run_mode_2 import (
    ADVISORY_KEYWORDS as RM2_ADVISORY_KEYWORDS,
)
from llm.rag.llm.run_mode_2 import (
    MATE_CLAIM_KEYWORDS as RM2_MATE_CLAIM_KEYWORDS,
)
from llm.rag.llm.run_mode_2 import (
    STRUCTURAL_KEYWORDS as RM2_STRUCTURAL_KEYWORDS,
)
from llm.rag.validators._fixtures import (
    ACCEPT_CARRIERS,
    DUAL_USE_PASSING_SAMPLES,
)
from llm.rag.validators._rules import (
    ADVISORY_KEYWORDS,
    DUAL_USE_TOKENS,
    ENGINE_LEXICAL_PATTERNS,
    ENGINE_LEXICAL_PHRASES,
    MATE_CLAIM_KEYWORDS,
    MATE_CLAIM_PATTERNS,
    MATE_INEVITABILITY_PHRASES,
    MATE_INEVITABILITY_SEMANTIC,
    MISSING_DATA_PHRASES,
    MOVE_ADVISORY_PATTERNS,
    MOVE_ALGEBRAIC_PATTERNS,
    SPECULATIVE_PATTERNS,
    STRUCTURAL_KEYWORDS,
)
from llm.rag.validators.mode_2_negative import validate_mode_2_negative


# ---------------------------------------------------------------------------
# TXI_ACC — accept-carrier sentences pass the lexical surface (PR #167 guard).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "required_phrase, carrier, where",
    ACCEPT_CARRIERS,
    ids=[f"{phrase}" for phrase, _carrier, _where in ACCEPT_CARRIERS],
)
def test_TXI_ACC_carrier_passes_lexical(
    required_phrase: str, carrier: str, where: str
) -> None:
    """For each (required_phrase, carrier) pair in
    ``ACCEPT_CARRIERS``, the carrier sentence MUST pass
    ``validate_mode_2_negative``.  PR #167 (2026-05-15) had a fallback
    emit "This is a forced mate" — the required word ``forced`` was
    present but the lexical surface rejected the ``forced mate``
    bigram.  This test catches that class of contradiction at PR
    review time, not at deploy time.

    The ``where`` field is a pointer to the fallback that emits the
    carrier — when this test fails, grep for it.
    """
    assert required_phrase.lower() in carrier.lower(), (
        f"Carrier {carrier!r} does not contain the required phrase "
        f"{required_phrase!r} — the fixture row is broken."
    )
    # Must not raise — if it does, we've reintroduced the PR #167 bug
    # class.  The error message names the offending pattern, which
    # tells the contributor which lexical token bites their carrier.
    validate_mode_2_negative(carrier)


def test_TXI_ACC_every_required_phrase_has_a_carrier() -> None:
    """Every entry in ``MATE_INEVITABILITY_PHRASES``,
    ``MATE_INEVITABILITY_SEMANTIC``, and ``MISSING_DATA_PHRASES`` must
    appear as a required-phrase in ``ACCEPT_CARRIERS``.  Without this
    coverage, a fallback could be authored against a required token
    that no test has shown to pass the lexical surface — the same
    setup as PR #167."""
    carrier_phrases = {required for required, _carrier, _where in ACCEPT_CARRIERS}
    all_required = (
        set(MATE_INEVITABILITY_PHRASES)
        | set(MATE_INEVITABILITY_SEMANTIC)
        | set(MISSING_DATA_PHRASES)
    )
    missing = all_required - carrier_phrases
    assert not missing, (
        f"Required semantic phrases without a row in ACCEPT_CARRIERS: "
        f"{sorted(missing)}.  Add a (phrase, carrier, where) tuple to "
        f"llm/rag/validators/_fixtures.py::ACCEPT_CARRIERS so the lexical "
        f"surface is proven to accept each required phrase's carrier."
    )


# ---------------------------------------------------------------------------
# TXI_DUR — DUAL_USE_TOKENS registry has a passing-sample row.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", list(DUAL_USE_TOKENS.keys()))
def test_TXI_DUR_dual_use_token_has_passing_samples(token: str) -> None:
    """Every entry in ``DUAL_USE_TOKENS`` (`_rules.py`) MUST have a
    corresponding row in ``DUAL_USE_PASSING_SAMPLES`` (`_fixtures.py`).

    The registry entry without a sample is documentation-only — it
    captures the rationale but has no regression coverage.  Adding a
    new dual-use token requires updating BOTH files in the same commit.
    """
    assert token in DUAL_USE_PASSING_SAMPLES, (
        f"DUAL_USE_TOKENS['{token}'] has no row in "
        f"DUAL_USE_PASSING_SAMPLES.  Add at least one coaching sentence "
        f"using the bare token to "
        f"llm/rag/validators/_fixtures.py::DUAL_USE_PASSING_SAMPLES "
        f"so the registry entry has a regression pin."
    )
    samples = DUAL_USE_PASSING_SAMPLES[token]
    assert samples, (
        f"DUAL_USE_PASSING_SAMPLES['{token}'] is empty.  Add at least "
        f"one coaching sentence containing the bare token."
    )


def test_TXI_DUR_no_orphan_passing_samples() -> None:
    """The reverse of ``test_TXI_DUR_dual_use_token_has_passing_samples`` —
    every key in ``DUAL_USE_PASSING_SAMPLES`` must appear in
    ``DUAL_USE_TOKENS``.  An orphan key means a sample list whose
    rationale doc has been lost."""
    sample_tokens = set(DUAL_USE_PASSING_SAMPLES.keys())
    registry_tokens = set(DUAL_USE_TOKENS.keys())
    orphans = sample_tokens - registry_tokens
    assert not orphans, (
        f"DUAL_USE_PASSING_SAMPLES has tokens with no DUAL_USE_TOKENS "
        f"registry entry: {sorted(orphans)}.  Either add the registry "
        f"entry in _rules.py or delete the orphan sample list."
    )


# ---------------------------------------------------------------------------
# TXI_SAM — DUAL_USE_PASSING_SAMPLES sentences actually pass the surface
#           where DUAL_USE_TOKENS says they should.
# ---------------------------------------------------------------------------


def _sample_param_id(sample: str) -> str:
    """Build a short, stable param ID — first 40 chars, with newlines collapsed."""
    return " ".join(sample.split())[:40]


_PASSING_SAMPLES_PARAMS: list[tuple[str, str]] = [
    (token, sample)
    for token, samples in DUAL_USE_PASSING_SAMPLES.items()
    for sample in samples
]


@pytest.mark.parametrize(
    "token, sample",
    _PASSING_SAMPLES_PARAMS,
    ids=[f"{token}:{_sample_param_id(sample)}" for token, sample in _PASSING_SAMPLES_PARAMS],
)
def test_TXI_SAM_lexical_accepts_dual_use_passing_samples(
    token: str, sample: str
) -> None:
    """For every passing-sample sentence, the lexical surface
    ``validate_mode_2_negative`` MUST accept it.  Catches a future
    contributor reintroducing a regex that re-blocks one of the
    accepted dual-use tokens.

    Surface-specific: this test covers the *lexical* surface only.
    The semantic / structural surfaces have their own ESV-conditioned
    or section-shape rules — they aren't relevant to dual-use lexical
    behaviour.
    """
    # Containment sanity: catches a fixture row that was edited but
    # whose sample no longer contains the token it's listed under.
    # Note: ``consider`` uses the substring inside ``consideration``
    # to demonstrate \bconsider\b does NOT match — accept either form.
    contains = token in sample.lower() or token.replace(" ", "") in sample.lower().replace(" ", "")
    assert contains, (
        f"DUAL_USE_PASSING_SAMPLES['{token}'] entry {sample!r} does "
        f"not contain the token text.  Fix the fixture row."
    )
    # Must not raise — the lexical surface must accept this sample.
    validate_mode_2_negative(sample)


# ---------------------------------------------------------------------------
# TXI_RM2 — run_mode_2 keyword imports match the published taxonomy.
# ---------------------------------------------------------------------------


def test_TXI_RM2_advisory_keywords_match_published() -> None:
    """``run_mode_2`` must consume the SAME ``ADVISORY_KEYWORDS`` that
    ``_rules.py`` publishes.  Catches a future drift where someone
    edits the repair-loop regex without touching ``_rules.py``."""
    assert RM2_ADVISORY_KEYWORDS == ADVISORY_KEYWORDS, (
        "run_mode_2.ADVISORY_KEYWORDS diverges from "
        "_rules.ADVISORY_KEYWORDS.  Re-import from _rules.py rather "
        "than open-coding the list."
    )


def test_TXI_RM2_mate_claim_keywords_match_published() -> None:
    assert RM2_MATE_CLAIM_KEYWORDS == MATE_CLAIM_KEYWORDS


def test_TXI_RM2_structural_keywords_match_published() -> None:
    assert RM2_STRUCTURAL_KEYWORDS == STRUCTURAL_KEYWORDS


def test_TXI_RM2_advisory_regex_built_from_keywords() -> None:
    """The compiled ``_ADVISORY_RE`` in ``run_mode_2`` must reject
    every keyword in ``ADVISORY_KEYWORDS`` (parameterised below) AND
    must NOT reject ``should`` (PR #170 retirement).  Pins the
    construction: ``re.compile(r"\\b(...)\\b", re.IGNORECASE)`` from
    the published list."""
    for kw in ADVISORY_KEYWORDS:
        assert _ADVISORY_RE.search(f"foo {kw} bar"), (
            f"_ADVISORY_RE in run_mode_2 does not match '{kw}' from "
            f"ADVISORY_KEYWORDS.  The repair-loop regex is stale."
        )
    # The retired token MUST NOT be matched — protects PR #170.
    assert not _ADVISORY_RE.search("you should develop"), (
        "_ADVISORY_RE in run_mode_2 matches 'should' — PR #170 "
        "retired it from ADVISORY_KEYWORDS but the regex still "
        "strips the token.  Restore the keyword set parity."
    )


def test_TXI_RM2_engine_phrases_subset() -> None:
    """The ``_ENGINE_PHRASES_FOR_STRIP`` tuple in ``run_mode_2`` must
    be a strict subset of ``ENGINE_LEXICAL_PHRASES`` (the aggressive
    path omits ``best move`` because ``_ADVISORY_RE`` covers it
    earlier).  Anything outside that subset is drift."""
    expected = tuple(p for p in ENGINE_LEXICAL_PHRASES if p != "best move")
    assert _ENGINE_PHRASES_FOR_STRIP == expected, (
        "run_mode_2._ENGINE_PHRASES_FOR_STRIP diverges from "
        "ENGINE_LEXICAL_PHRASES minus 'best move'.  If the published "
        "list changes, update the derivative in lockstep."
    )


# ---------------------------------------------------------------------------
# TXI_OVL — inter-list overlap matches the documented dual-use registry.
# ---------------------------------------------------------------------------


# Overlap-test whitelist: patterns whose bare form contains regex
# constructs (character classes, quantifiers, capture groups) and so
# cannot be reduced to a plain word for DUAL_USE_TOKENS lookup.  If a
# future overlap involves one of these patterns, the contributor must
# either rewrite the pattern as a plain-word form OR add an
# explicit allow-row here with the rationale.  The set is empty
# today because no such pattern currently appears in two
# ``*_PATTERNS`` lists.
_OVERLAP_REGEX_CONSTRUCT_ALLOW: frozenset[str] = frozenset()


def test_TXI_OVL_lexical_pattern_overlap_matches_dual_use_registry() -> None:
    """If a forbidden lexical pattern appears in more than one
    ``*_PATTERNS`` set, the overlap MUST be documented in
    ``DUAL_USE_TOKENS``.

    Two surfaces independently rejecting the same token is sometimes
    deliberate (``\\bconsider\\b`` is in both SPECULATIVE_PATTERNS and
    MOVE_ADVISORY_PATTERNS — defence in depth, see DUAL_USE_TOKENS
    entry).  An *undocumented* overlap means retiring the token from
    one surface would silently leave it in the other — the same
    failure mode PR #170 had to chase across three test files.

    Limitation: the bare-word extraction below cannot reduce patterns
    that contain regex constructs (e.g. ``\\b[KQRBN]?[a-h][1-8]\\b``,
    ``\\bforce(?:d)? mate\\b``) to a single token for DUAL_USE_TOKENS
    lookup.  Such patterns must be enumerated in
    ``_OVERLAP_REGEX_CONSTRUCT_ALLOW`` above if and when they enter
    the overlap set — silently dropping them would let a future
    multi-word forbidden bigram appear in two surfaces without any
    documented rationale.
    """
    all_lists: dict[str, tuple[str, ...]] = {
        "ENGINE_LEXICAL_PATTERNS": ENGINE_LEXICAL_PATTERNS,
        "MOVE_ALGEBRAIC_PATTERNS": MOVE_ALGEBRAIC_PATTERNS,
        "MOVE_ADVISORY_PATTERNS": MOVE_ADVISORY_PATTERNS,
        "SPECULATIVE_PATTERNS": SPECULATIVE_PATTERNS,
        "MATE_CLAIM_PATTERNS": MATE_CLAIM_PATTERNS,
    }
    pattern_to_lists: dict[str, list[str]] = {}
    for list_name, patterns in all_lists.items():
        for pattern in patterns:
            pattern_to_lists.setdefault(pattern, []).append(list_name)
    overlaps = {p: lists for p, lists in pattern_to_lists.items() if len(lists) > 1}
    if not overlaps:
        return  # No overlap = nothing to verify against the registry.

    undocumented: list[tuple[str, list[str]]] = []
    unreducible: list[tuple[str, list[str]]] = []
    for pattern, lists in overlaps.items():
        bare = pattern.strip("\\b").replace(r"\b", "")
        is_plain_word = bare.isascii() and all(c.isalpha() or c.isspace() for c in bare)
        if not is_plain_word:
            # Bare form is not a plain word — must be on the explicit
            # allow-list so the test cannot silently lose coverage.
            if pattern not in _OVERLAP_REGEX_CONSTRUCT_ALLOW:
                unreducible.append((pattern, lists))
            continue
        if bare not in DUAL_USE_TOKENS:
            undocumented.append((pattern, lists))

    assert not unreducible, (
        f"Lexical patterns appear in multiple *_PATTERNS sets but their "
        f"regex form cannot be reduced to a plain-word lookup against "
        f"DUAL_USE_TOKENS: {unreducible}.  Either (a) split the pattern "
        f"into plain-word components, or (b) add the regex to "
        f"_OVERLAP_REGEX_CONSTRUCT_ALLOW above with an inline comment "
        f"explaining the documented rationale + the PR number."
    )
    assert not undocumented, (
        f"Lexical patterns appear in multiple *_PATTERNS sets without "
        f"a DUAL_USE_TOKENS registry entry: {undocumented}.  Either "
        f"remove the duplicate from one of the sets, or document the "
        f"intentional overlap in _rules.DUAL_USE_TOKENS with rationale + "
        f"the PR number that established the asymmetry."
    )


def test_TXI_OVL_no_lexical_forbidden_matches_required_substring() -> None:
    """Cross-surface contradiction guard — PR #167 class.

    For each required substring (semantic-required, validate_output-
    required), construct a *minimal* sentence containing the substring
    and verify no lexical forbidden pattern matches it.  An overlap
    here means a fallback emitting the required substring in its
    natural carrier would be rejected by the lexical surface.

    The pattern-by-pattern PR #167 fix was to switch
    ``"this is a forced mate"`` → ``"mate is inevitable"``.  This test
    catches the next instance of that shape at CI time.
    """
    all_forbidden = (
        *ENGINE_LEXICAL_PATTERNS,
        *MOVE_ALGEBRAIC_PATTERNS,
        *MOVE_ADVISORY_PATTERNS,
        *SPECULATIVE_PATTERNS,
        *MATE_CLAIM_PATTERNS,
    )
    contradictions: list[tuple[str, str]] = []
    for required in (
        *MATE_INEVITABILITY_PHRASES,
        *MATE_INEVITABILITY_SEMANTIC,
        *MISSING_DATA_PHRASES,
    ):
        for pattern in all_forbidden:
            # If the forbidden pattern matches the bare required
            # substring, that's a direct contradiction.  Synthetic
            # one-word check; the ``ACCEPT_CARRIERS`` test above
            # exercises the realistic-carrier form.
            if re.search(pattern, required, re.IGNORECASE):
                contradictions.append((pattern, required))
    assert not contradictions, (
        f"Forbidden lexical patterns directly match required-by-semantic "
        f"substrings: {contradictions}.  This is the PR #167 (2026-05-15) "
        f"failure class — any fallback emitting the required substring "
        f"would trip the lexical reject.  Choose a different required "
        f"vocabulary or remove the overlap."
    )
