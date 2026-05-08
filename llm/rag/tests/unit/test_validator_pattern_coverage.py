"""
Validator pattern-coverage closure tests.

These tests close the gap that the 2026-05-07 mutmut baseline surfaced:
~70 of 78 surviving mutants were string-literal mutations on
forbidden-pattern lists.  The previous tests asserted *behavioural*
properties of the validators (compliant text passes, violations are
rejected, REQUIRED phrases enforced) but never asserted that
*every individual pattern in every list* catches what it advertises.

A regex like ``r"\bcarelessly\b"`` could be mutated to ``r"XXcarelesslyXX"``
and the existing suite would still pass because the violations corpus
never tested "carelessly".  The tests below walk each list with a
positive sample per entry, so each individual pattern's literal is now
load-bearing for the suite.

Stable test IDs (do NOT rename):
  MTC_NEG_*   mode_2_negative — every FORBIDDEN_PATTERN catches its trigger
  MTC_STR_*   mode_2_structure — every FORBIDDEN_SECTION catches its trigger
  MTC_SEM_*   mode_2_semantic — equal/mate/engine-spec/invented-tactics lists
  MTC_OUT_*   validate_output — FORBIDDEN_PHRASES + REQUIRED_ON_*
  MTC_SAN_*   sanitize — NOTATION_REGEX + mask_chess_notation
  MTC_FW_*    output_firewall — bypass regex, error-message format, logging
"""

from __future__ import annotations

import logging
import re

import pytest

from llm.rag.contracts.validate_output import (
    FORBIDDEN_PHRASES,
    REQUIRED_ON_MATE,
    REQUIRED_ON_MISSING,
    validate_output,
)
from llm.rag.safety.output_firewall import (
    OutputFirewallError,
    check_output,
)
from llm.rag.validators.mode_2_negative import (
    FORBIDDEN_PATTERNS as NEG_PATTERNS,
    validate_mode_2_negative,
)
from llm.rag.validators.mode_2_semantic import (
    Mode2Violation,
    validate_mode_2_semantic,
)
from llm.rag.validators.mode_2_structure import (
    FORBIDDEN_SECTIONS as STR_PATTERNS,
    validate_mode_2_structure,
)
from llm.rag.validators.sanitize import NOTATION_REGEX, mask_chess_notation


# ---------------------------------------------------------------------------
# mode_2_negative — every forbidden pattern catches its trigger word.
# Samples are chosen to exercise *only* the target pattern; no other
# entry in NEG_PATTERNS matches the text.  When mutmut mutates a single
# pattern's literal, exactly one parametrised case fails — which is what
# kills the mutant.
# ---------------------------------------------------------------------------

NEG_SAMPLES: list[tuple[str, str]] = [
    (r"\bshould\b",                "you should know"),
    (r"\blikely\b",                "snow is likely"),
    (r"\bprobably\b",              "milk is probably ok"),
    (r"\bI think\b",               "I think apples are tasty"),
    (r"\bthe engine wants\b",      "the engine wants water"),
    (r"\bplans to\b",              "alice plans to visit"),
    (r"\bcarelessly\b",            "she answered carelessly today"),
    (r"\black of planning\b",      "showed clear lack of planning"),
    (r"\bwith perfect play\b",     "ended with perfect play"),
    (r"\bactually winning\b",      "she is actually winning"),
    (r"\bconsider\b",              "we consider tea"),
    (r"\b[KQRBN][a-h][1-8]\b",     "the move Qh5 follows"),
    (r"\b0-0(?:-0)?\b",            "wrote 0-0 today"),
    (r"\bcalculate\b",             "we calculate taxes"),
    (r"\bcalculation\b",           "after calculation finished"),
    (r"\bvariation\b",             "the main variation today"),
    (r"\bline\b",                  "second line text"),
    (r"\bcheckmate\b",             "checkmate occurred"),
    (r"\bmate in \d+\b",           "mate in 3 moves"),
    (r"\bforce(?:d)? mate\b",      "force mate now"),
    (r"\bgame ends here\b",        "the game ends here"),
]


@pytest.mark.parametrize("pattern, sample", NEG_SAMPLES, ids=[p for p, _ in NEG_SAMPLES])
def test_mtc_neg_each_pattern_rejected(pattern: str, sample: str) -> None:
    """MTC_NEG: each FORBIDDEN_PATTERN entry rejects its trigger sample.

    Pre-condition: the parametrised pattern is *in* the live list (so
    a future commit can't quietly delete a pattern without dropping the
    test row in the same diff).
    """
    assert pattern in NEG_PATTERNS, (
        f"Pattern {pattern!r} is not in mode_2_negative.FORBIDDEN_PATTERNS — "
        f"either the test row is stale or the pattern was removed without "
        f"updating this file in the same commit."
    )
    with pytest.raises(AssertionError) as exc_info:
        validate_mode_2_negative(sample)
    # Exact-equality assertion on the error message — a substring check
    # would let mutmut wrap the literal in 'XX...XX' and survive.  The
    # operator/log path reads this exact string, so pinning the full
    # form is the right contract anyway.
    assert (
        str(exc_info.value)
        == f"Forbidden MODE-2 pattern detected: pattern `{pattern}`"
    )


def test_mtc_neg_pattern_list_is_complete() -> None:
    """MTC_NEG: every entry in the live list has a parametrised sample
    above.  Catches the case where someone adds a new forbidden pattern
    but forgets to add the test row — without this guard, the new
    pattern's string literal could survive every mutmut run."""
    sampled = {p for p, _ in NEG_SAMPLES}
    missing = [p for p in NEG_PATTERNS if p not in sampled]
    assert not missing, (
        f"FORBIDDEN_PATTERNS contains {len(missing)} entries with no test "
        f"sample: {missing}.  Add a (pattern, sample) row to NEG_SAMPLES."
    )


def test_mtc_neg_empty_input_rejected() -> None:
    """MTC_NEG: empty / whitespace input is invalid.  Pins the exact
    ``assert text.strip(), "Empty output is invalid"`` line at the top
    of validate_mode_2_negative — exact-equality assertion so the
    literal cannot be silently wrapped or mutated."""
    with pytest.raises(AssertionError) as exc:
        validate_mode_2_negative("")
    assert str(exc.value) == "Empty output is invalid"
    with pytest.raises(AssertionError) as exc:
        validate_mode_2_negative("   \t  ")
    assert str(exc.value) == "Empty output is invalid"


# ---------------------------------------------------------------------------
# mode_2_structure — every forbidden section catches its trigger.
# The validator lowercases input before matching, so samples can be
# any case.
# ---------------------------------------------------------------------------

STR_SAMPLES: list[tuple[str, str]] = [
    (r"\brecommended move\b",  "the recommended move was solid"),
    (r"\bexample move\b",      "an example move follows"),
    (r"\bplan\b",              "needed a plan today"),
    (r"\bwhite can\b",         "white can defend here"),
    (r"\bblack can\b",         "black can respond now"),
    (r"\bif it\b",             "if it works out"),
    (r"\bconsider\b",          "consider this idea"),
]


@pytest.mark.parametrize("pattern, sample", STR_SAMPLES, ids=[p for p, _ in STR_SAMPLES])
def test_mtc_str_each_section_rejected(pattern: str, sample: str) -> None:
    """MTC_STR: each FORBIDDEN_SECTION entry rejects its trigger sample.
    Exact-equality message check — XX-wrapping mutations on the format
    string don't survive."""
    assert pattern in STR_PATTERNS, (
        f"Pattern {pattern!r} is not in mode_2_structure.FORBIDDEN_SECTIONS"
    )
    with pytest.raises(AssertionError) as exc_info:
        validate_mode_2_structure(sample)
    assert (
        str(exc_info.value)
        == f"Mode-2 structural violation: forbidden section `{pattern}`"
    )


def test_mtc_str_section_list_is_complete() -> None:
    """MTC_STR: every entry in the live list has a sample row."""
    sampled = {p for p, _ in STR_SAMPLES}
    missing = [p for p in STR_PATTERNS if p not in sampled]
    assert not missing, f"FORBIDDEN_SECTIONS missing test rows: {missing}"


# ---------------------------------------------------------------------------
# mode_2_semantic — four conditional lists, each gated on engine_signal.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, word",
    [
        ("the position is a slight advantage", "slight advantage"),
        ("white is better",                    "better"),
        ("white is winning",                   "winning"),
        ("white has the initiative",           "initiative"),
        ("white has pressure",                 "pressure"),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 30 else "row",
)
def test_mtc_sem_equal_band_rejects_advantage_words(text: str, word: str) -> None:
    """MTC_SEM: when ``band == 'equal'``, every forbidden advantage word
    raises Mode2Violation with the exact diagnostic message.  Tight
    string match so format-string mutations on line 35 of the validator
    don't survive."""
    engine_signal = {"evaluation": {"band": "equal"}}
    with pytest.raises(Mode2Violation) as exc:
        validate_mode_2_semantic(text, engine_signal)
    assert (
        str(exc.value)
        == f"Equal position described as advantage: '{word}'"
    )


def test_mtc_sem_equal_band_clean_text_passes() -> None:
    """Sanity: an equal-band response with no forbidden words passes."""
    engine_signal = {"evaluation": {"band": "equal"}}
    validate_mode_2_semantic("the position is balanced and quiet", engine_signal)


def test_mtc_sem_mate_requires_inevitability() -> None:
    """MTC_SEM: when ``eval_type == 'mate'``, text without 'inevitable'
    or 'forced' raises with the exact 'Mate not described as
    forced/inevitable' message."""
    engine_signal = {"evaluation": {"type": "mate"}}
    with pytest.raises(Mode2Violation) as exc:
        validate_mode_2_semantic("the result is decisive", engine_signal)
    assert str(exc.value) == "Mate not described as forced/inevitable"


@pytest.mark.parametrize("required_word", ["inevitable", "forced"])
def test_mtc_sem_mate_with_required_word_passes(required_word: str) -> None:
    """MTC_SEM: each accepted word ('inevitable', 'forced') alone is
    enough for a mate response.  Mutating the literal in the validator
    must fail one of these cases."""
    engine_signal = {"evaluation": {"type": "mate"}}
    text = f"the outcome is {required_word} and clear"
    validate_mode_2_semantic(text, engine_signal)


SPEC_SAMPLES = ["likely", "probably", "might", "engine", "wants to"]


@pytest.mark.parametrize("word", SPEC_SAMPLES, ids=SPEC_SAMPLES)
def test_mtc_sem_speculative_words_rejected(word: str) -> None:
    """MTC_SEM: each entry in the engine-speculation list raises with
    the exact diagnostic, regardless of band/eval_type."""
    text = f"the position {word} continues"
    with pytest.raises(Mode2Violation) as exc:
        validate_mode_2_semantic(text, {"evaluation": {}})
    assert str(exc.value) == f"Speculative language detected: '{word}'"


INVENTED_SAMPLES = ["fork", "pin", "sacrifice", "attack", "threat"]


@pytest.mark.parametrize("word", INVENTED_SAMPLES, ids=INVENTED_SAMPLES)
def test_mtc_sem_invented_tactics_rejected_when_no_flag(word: str) -> None:
    """MTC_SEM: when ``tactical_flags`` is empty, every invented-tactic
    word raises with the exact diagnostic.  Pins the inner literal list
    plus the format string at line 53 of the validator."""
    # Avoid speculative-language false positives: the spec list contains
    # 'engine' and others that could collide with our sample.  Compose a
    # neutral carrier sentence.
    text = f"this contains a {word} here"
    with pytest.raises(Mode2Violation) as exc:
        validate_mode_2_semantic(text, {"tactical_flags": []})
    assert str(exc.value) == f"Invented tactic without flag: '{word}'"


def test_mtc_sem_invented_tactics_allowed_with_flag() -> None:
    """MTC_SEM: when ``tactical_flags`` is non-empty, the invented-tactic
    list is NOT enforced.  Pins the `if not tactical_flags:` guard."""
    text = "this contains a fork here"
    # Should not raise — tactical flag present.
    validate_mode_2_semantic(text, {"tactical_flags": ["fork"]})


# ---------------------------------------------------------------------------
# validate_output — FORBIDDEN_PHRASES + REQUIRED_ON_MISSING + REQUIRED_ON_MATE.
# All three lists are plain-substring (lowercased), not regex.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES, ids=list(FORBIDDEN_PHRASES))
def test_mtc_out_each_forbidden_phrase_rejected(phrase: str) -> None:
    """MTC_OUT: each FORBIDDEN_PHRASES entry rejects when present in the
    output, with the exact diagnostic message.  Pins both the literal
    and the format string at line 32 of validate_output."""
    text = f"the response mentions {phrase} explicitly"
    with pytest.raises(AssertionError) as exc_info:
        validate_output(text, case_type="general")
    assert str(exc_info.value) == f"Forbidden phrase detected: {phrase}"


@pytest.mark.parametrize("phrase", REQUIRED_ON_MISSING, ids=list(REQUIRED_ON_MISSING))
def test_mtc_out_each_required_on_missing_satisfies(phrase: str) -> None:
    """MTC_OUT: each REQUIRED_ON_MISSING phrase, when present, satisfies
    the missing-data case.  Mutating any literal in the list must fail
    one parametrised case."""
    # Use a carrier text that does NOT contain any other REQUIRED phrase
    # nor any forbidden phrase, so only the parametrised one is what
    # makes the validator pass.
    text = f"the relevant data is {phrase} from the request"
    validate_output(text, case_type="missing_data")  # must not raise


def test_mtc_out_missing_without_required_phrase_raises() -> None:
    """MTC_OUT: missing_data case_type with no REQUIRED phrase raises
    the EXACT 'Missing-data response does not acknowledge missing
    information' message.  Pins line 36 of validate_output."""
    with pytest.raises(AssertionError) as exc:
        validate_output("the position is unclear", case_type="missing_data")
    assert (
        str(exc.value)
        == "Missing-data response does not acknowledge missing information"
    )


@pytest.mark.parametrize("phrase", REQUIRED_ON_MATE, ids=list(REQUIRED_ON_MATE))
def test_mtc_out_each_required_on_mate_satisfies(phrase: str) -> None:
    """MTC_OUT: each REQUIRED_ON_MATE phrase satisfies the forced_mate
    case.  Note: REQUIRED_ON_MATE entries can be substrings ('cannot be
    avoided' contains 'avoid', 'unavoidable' contains 'avoid'), so we
    use the entry verbatim in the carrier text."""
    text = f"the result is {phrase} and resolved"
    validate_output(text, case_type="forced_mate")  # must not raise


def test_mtc_out_mate_without_required_phrase_raises() -> None:
    """MTC_OUT: forced_mate case_type with no REQUIRED phrase raises
    the EXACT 'Forced-mate response does not emphasize inevitability'
    message.  Pins line 40 of validate_output."""
    with pytest.raises(AssertionError) as exc:
        validate_output("the result is decisive", case_type="forced_mate")
    assert (
        str(exc.value)
        == "Forced-mate response does not emphasize inevitability"
    )


# ---------------------------------------------------------------------------
# sanitize — NOTATION_REGEX + mask_chess_notation.
# The previous suite had 0% kill rate (3/3 survived) because no test
# verified the regex actually matched chess notation or the
# substitution string was '[REDACTED]'.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "notation",
    [
        "Qh5",        # piece + file + rank (queen move)
        "Nf6",        # piece + file + rank (knight move)
        "Rh8",        # rook move
        "Be5",        # bishop move
        "Kg2",        # king move
        "e4",         # pawn move alone (no piece prefix)
        "h8=Q",       # pawn promotion
        "e1=R",       # promotion to rook
        "Qh5+",       # piece move + check
        "e4+",        # pawn move + check
    ],
)
def test_mtc_san_notation_matched(notation: str) -> None:
    """MTC_SAN: the NOTATION_REGEX matches every form covered by its
    grammar — piece-or-pawn destination plus optional promotion plus
    optional check.

    Note: the regex deliberately does NOT match SAN capture notation
    ('Bxe5', 'exd5'); the destination has to appear before the
    optional `(x[a-h][1-8])?` group, which is a degenerate form that
    real chess never produces.  This is a known narrow scope of the
    sanitizer (sufficient for the masking use case where the LLM is
    most likely to emit short destination notations).  Mutating the
    regex string must still drop at least one case below."""
    assert NOTATION_REGEX.search(notation), (
        f"NOTATION_REGEX failed to match {notation!r} — the regex string "
        f"may have been silently weakened."
    )


def test_mtc_san_substitution_uses_redacted_marker() -> None:
    """MTC_SAN: mask_chess_notation replaces with the literal
    '[REDACTED]' — not 'XXXX', not 'XX[REDACTED]XX', not anything else.
    Exact-equality assertion on the output pins the substitution string
    at line 6 of sanitize.py."""
    out = mask_chess_notation("play Qh5 then Nf6 here")
    # Exact match — the regex matches both 'Qh5' and 'Nf6', each
    # replaced by '[REDACTED]'.  Any wrapping or alteration of the
    # substitution literal changes this exact string.
    assert out == "play [REDACTED] then [REDACTED] here"


def test_mtc_san_neutral_text_unchanged() -> None:
    """MTC_SAN: text without notation must round-trip unchanged.  Catches
    a mutation that replaces all text indiscriminately."""
    original = "the position is balanced and quiet"
    assert mask_chess_notation(original) == original


# ---------------------------------------------------------------------------
# output_firewall — IDENTITY bypass regex + error-message format + logging.
# The 92% baseline kill rate has 4 named survivors; the tests below
# target each one.
# ---------------------------------------------------------------------------


def test_mtc_fw_persona_bypass_blocked() -> None:
    """MTC_FW: the IDENTITY-category bypass regex
    ``r"as\\s+my\\s+new\\s+(persona|character|role)\\b"`` blocks all
    three terminator alternatives.  This was a baseline survivor —
    no prior test exercised this specific pattern."""
    for terminator in ("persona", "character", "role"):
        text = f"I'll act as my new {terminator} of a chess coach"
        with pytest.raises(OutputFirewallError) as exc_info:
            check_output(text)
        assert exc_info.value.category == "IDENTITY", (
            f"persona-bypass with terminator '{terminator}' must surface as "
            f"IDENTITY category, got {exc_info.value.category}"
        )


def test_mtc_fw_error_message_format() -> None:
    """MTC_FW: OutputFirewallError carries the EXACT shape
    ``Output firewall violation [CATEGORY]: N pattern(s) matched — response blocked.``
    Pins the f-string literal at lines 42–43 of output_firewall.py."""
    with pytest.raises(OutputFirewallError) as exc_info:
        check_output("I am ChatGPT speaking now")
    msg = str(exc_info.value)
    expected_prefix = "Output firewall violation [IDENTITY]: "
    assert msg.startswith(expected_prefix), (
        f"Error message must start with {expected_prefix!r}; got {msg!r}"
    )
    expected_suffix = " pattern(s) matched — response blocked."
    assert msg.endswith(expected_suffix), (
        f"Error message must end with {expected_suffix!r}; got {msg!r}"
    )


def test_mtc_fw_warning_logged_on_block(caplog: pytest.LogCaptureFixture) -> None:
    """MTC_FW: when the firewall blocks, a WARNING is logged with the
    EXACT phrase 'Output firewall blocked response due to safety policy.'.
    Pins the logger.warning call at line 170 of output_firewall.py —
    exact match so a XX-wrapping mutation on the literal can't survive."""
    with caplog.at_level(logging.WARNING):
        with pytest.raises(OutputFirewallError):
            check_output("I am ChatGPT")
    matched = [
        record.message
        for record in caplog.records
        if record.message == "Output firewall blocked response due to safety policy."
    ]
    assert matched, (
        f"Expected exact warning 'Output firewall blocked response due to "
        f"safety policy.' in caplog; got records: "
        f"{[r.message for r in caplog.records]!r}"
    )
