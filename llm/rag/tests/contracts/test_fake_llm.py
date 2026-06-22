import pytest

from llm.rag.llm.fake import FakeLLM
from llm.rag.llm.run_mode_2 import run_mode_2

PROMPT = "dummy prompt"

# Neutral ESV — disables every ESV-gated semantic check.  Each test below
# pins a different validator's behaviour by varying the FakeLLM mode +
# case_type; keeping ESV neutral isolates those gates from the
# ESV-conditioned semantic surface (covered separately in
# test_run_mode_2_cascades.py).
_NEUTRAL_ESV = {
    "evaluation": {"type": "cp", "value": 0},
    "tactical_flags": ["any"],
}


def test_compliant_output_passes():
    llm = FakeLLM(mode="compliant")
    run_mode_2(
        llm=llm,
        prompt=PROMPT,
        case_type="forced_mate",
        engine_signal=_NEUTRAL_ESV,
    )


def test_forbidden_phrase_fails():
    # FakeLLM(mode="forbidden_phrase") emits
    #   "Stockfish shows the best move here leads to mate in 3."
    # As of 2026-06-22 validate_mode_2_negative carries ``\bstockfish\b``
    # (ENGINE_LEXICAL_PATTERNS now mirrors the phrase set onto the live
    # boundary), and ENGINE_LEXICAL precedes MATE_CLAIM in FORBIDDEN_PATTERNS,
    # so "Stockfish" is the first forbidden token the chain hits — ahead of
    # the ``\bmate in \d+\b`` pattern and the validate_output FORBIDDEN_PHRASES
    # path.  Pin both the prefix and the specific token so a refactor that
    # swaps which validator catches this case fails loudly here.
    llm = FakeLLM(mode="forbidden_phrase")
    with pytest.raises(AssertionError, match=r"Forbidden MODE-2 pattern detected.*stockfish"):
        run_mode_2(
            llm=llm,
            prompt=PROMPT,
            case_type="forced_mate",
            engine_signal=_NEUTRAL_ESV,
        )


def test_missing_data_violation_fails():
    # FakeLLM(mode="missing_data_violation") emits a sentence that contains
    # no forbidden tokens and no required REQUIRED_ON_MISSING phrase.
    # validate_mode_2_negative + validate_mode_2_structure pass; the failure
    # surfaces from contracts.validate_output's missing-data branch.
    llm = FakeLLM(mode="missing_data_violation")
    with pytest.raises(
        AssertionError,
        match=r"Missing-data response does not acknowledge missing information",
    ):
        run_mode_2(
            llm=llm,
            prompt=PROMPT,
            case_type="missing_data",
            engine_signal=_NEUTRAL_ESV,
        )


def test_mate_softening_fails():
    # FakeLLM(mode="mate_softening") emits a benign sentence with no
    # REQUIRED_ON_MATE phrase (``inevitable`` / ``cannot be avoided`` /
    # ``unavoidable``).  validate_mode_2_negative + validate_mode_2_structure
    # pass; the failure surfaces from contracts.validate_output's
    # forced-mate branch.
    llm = FakeLLM(mode="mate_softening")
    with pytest.raises(
        AssertionError,
        match=r"Forced-mate response does not emphasize inevitability",
    ):
        run_mode_2(
            llm=llm,
            prompt=PROMPT,
            case_type="forced_mate",
            engine_signal=_NEUTRAL_ESV,
        )
