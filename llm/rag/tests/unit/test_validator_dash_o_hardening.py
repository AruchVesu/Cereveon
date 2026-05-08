"""Regression: validators must survive ``python -O`` bytecode optimization.

Under ``python -O`` (or ``-OO``, or ``PYTHONOPTIMIZE``), bare ``assert``
statements are stripped from the bytecode at compile time.  Any
production-path validator that uses ``assert <cond>, "<msg>"`` therefore
**silently disappears** when the same code runs under -O — leaving the
caller with no exception and no signal that the input was invalid.

This test pins the choice in
``llm/rag/validators/mode_2_negative.py`` to use explicit
``if not text.strip(): raise AssertionError(...)`` rather than a bare
``assert``.  If a future contributor reverses that swap (e.g. while
"simplifying" the function), this test fails — under -O, an empty
string would otherwise pass the empty-input gate undetected and only
fail on the FORBIDDEN_PATTERNS scan, which won't fire for the empty
string at all, producing a silent ``None`` return.

Stable test ID (do NOT rename): VAL_DASH_O_01
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.dash_o_hardening
def test_val_dash_o_01_empty_input_rejected_under_dash_o() -> None:
    """VAL_DASH_O_01: ``validate_mode_2_negative('')`` must raise
    AssertionError even when Python is started with ``-O``."""
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            (
                "from llm.rag.validators.mode_2_negative import "
                "validate_mode_2_negative; validate_mode_2_negative('')"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0, (
        "Empty-input gate disappeared under -O — did someone swap the "
        "explicit `raise AssertionError` back to a bare `assert`?  "
        f"rc={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "AssertionError" in result.stderr, (
        "Expected AssertionError on empty input under -O; got "
        f"stderr={result.stderr!r}.  The function may be raising a "
        "different exception type, which would silently break callers "
        "that `except AssertionError`."
    )
    assert "Empty output is invalid" in result.stderr, (
        "AssertionError fired but the message has changed.  Update this "
        f"test if the message change is intentional.  stderr={result.stderr!r}"
    )
