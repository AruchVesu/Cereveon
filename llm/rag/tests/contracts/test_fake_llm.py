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
    llm = FakeLLM(mode="forbidden_phrase")
    try:
        run_mode_2(
            llm=llm,
            prompt=PROMPT,
            case_type="forced_mate",
        )
        assert False, "Expected failure"
    except AssertionError:
        pass


def test_missing_data_violation_fails():
    llm = FakeLLM(mode="missing_data_violation")
    try:
        run_mode_2(
            llm=llm,
            prompt=PROMPT,
            case_type="missing_data",
        )
        assert False, "Expected failure"
    except AssertionError:
        pass


def test_mate_softening_fails():
    llm = FakeLLM(mode="mate_softening")
    try:
        run_mode_2(
            llm=llm,
            prompt=PROMPT,
            case_type="forced_mate",
        )
        assert False, "Expected failure"
    except AssertionError:
        pass
