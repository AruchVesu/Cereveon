import pytest

from llm.rag.llm.fake import FakeLLM
from llm.rag.llm.run_mode_2 import run_mode_2

PROMPT = "dummy prompt"


def test_compliant_output_passes():
    llm = FakeLLM(mode="compliant")
    run_mode_2(
        llm=llm,
        prompt=PROMPT,
        case_type="forced_mate",
    )


def test_forbidden_phrase_fails():
    # FakeLLM(mode="forbidden_phrase") emits
    #   "Stockfish shows the best move here leads to mate in 3."
    # validate_mode_2_negative runs first in the validator chain and matches
    # the FORBIDDEN_PATTERNS entry ``\bmate in \d+\b`` before validate_output
    # gets a chance to catch the "stockfish" / "best move" FORBIDDEN_PHRASES
    # entries.  Pin both the prefix and the specific pattern so a refactor
    # that swaps which validator catches this case fails loudly here.
    llm = FakeLLM(mode="forbidden_phrase")
    with pytest.raises(AssertionError, match=r"Forbidden MODE-2 pattern detected.*mate in"):
        run_mode_2(
            llm=llm,
            prompt=PROMPT,
            case_type="forced_mate",
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
        )
