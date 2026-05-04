"""
Defense-in-depth tests for the _safe_log() sanitiser used in the
engine_signal extractor and the events router.

Findings covered
────────────────
LOG-01  Untrusted Stockfish JSON values (eval_type, last_move_quality,
        eval value) reach logger calls in extract_engine_signal.py.
        Without sanitisation, a malformed payload containing CR/LF
        could forge a fake log line (CWE-117).

LOG-02  Untrusted HTTP request body fields (CoachFeedbackRequest.
        session_fen, GameFinishRequest.game_id) reach logger calls in
        events/router.py.  Same CWE-117 concern with an
        attacker-controlled field instead of an attacker-controlled
        engine response.

The sanitiser must:

  1. Drop CR (\\r), LF (\\n), NEL (U+0085) and the Unicode line
     separators U+2028 / U+2029 — these are the code points loggers
     and log aggregators may interpret as line breaks.
  2. Wrap non-string values via repr() so primitives stay readable.
  3. Truncate to a max length so a giant payload cannot bloat a
     single log line.
  4. Be referentially transparent — same input always yields the
     same output.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("SECA_ENV", "dev")

from llm.rag.engine_signal.extract_engine_signal import (
    _safe_log as _engine_safe_log,
)
from llm.seca.events.router import _safe_log as _router_safe_log


_BOTH_SANITISERS = [_engine_safe_log, _router_safe_log]


class TestSafeLogSanitiser(unittest.TestCase):
    """Both sanitisers (one per module) must enforce the same contract.
    They are duplicated by design — the explicit replace chain is what
    makes them visible to CodeQL's taint tracker, and a shared helper
    in a third module would obscure the sanitisation chain at the call
    site."""

    def test_strips_cr(self):
        for fn in _BOTH_SANITISERS:
            self.assertNotIn("\r", fn("a\rb"))

    def test_strips_lf(self):
        for fn in _BOTH_SANITISERS:
            self.assertNotIn("\n", fn("a\nb"))

    def test_strips_nel_u0085(self):
        for fn in _BOTH_SANITISERS:
            self.assertNotIn("\x85", fn("a\x85b"))

    def test_strips_u2028_line_separator(self):
        for fn in _BOTH_SANITISERS:
            self.assertNotIn(" ", fn("a b"))

    def test_strips_u2029_paragraph_separator(self):
        for fn in _BOTH_SANITISERS:
            self.assertNotIn(" ", fn("a b"))

    def test_strips_classic_log_forge_payload(self):
        """The canonical CWE-117 payload — a CR/LF followed by a fake
        log header — must not appear verbatim in the sanitised output."""
        payload = "X\r\n[CRITICAL] Backdoor open"
        for fn in _BOTH_SANITISERS:
            out = fn(payload, max_len=200)
            self.assertNotIn("\r", out)
            self.assertNotIn("\n", out)

    def test_handles_non_string_primitives(self):
        for fn in _BOTH_SANITISERS:
            self.assertEqual(fn(42), "42")
            self.assertEqual(fn(True), "True")
            self.assertEqual(fn(None), "None")

    def test_truncates_long_input(self):
        big = "x" * 10_000
        for fn in _BOTH_SANITISERS:
            self.assertLessEqual(len(fn(big, max_len=80)), 80)

    def test_referentially_transparent(self):
        for fn in _BOTH_SANITISERS:
            self.assertEqual(fn("hello"), fn("hello"))

    def test_handles_dict_input_safely(self):
        """Stockfish's `errors` field could be any JSON value.  A dict
        with a CR/LF-laced key/value must still come out single-line."""
        payload = {"key\r\nFAKE": "val\r\nFAKE"}
        for fn in _BOTH_SANITISERS:
            out = fn(payload, max_len=200)
            self.assertNotIn("\r", out)
            self.assertNotIn("\n", out)

    def test_empty_string_returns_empty_repr(self):
        # repr("") == "''" — non-empty by design so the log reads
        # "value=''" not "value=" (which would be ambiguous).
        for fn in _BOTH_SANITISERS:
            self.assertEqual(fn(""), "''")

    def test_unicode_text_passthrough(self):
        """Ordinary Unicode text (no line separators) must round-trip.
        Sanitisation should not be eager — it strips line-ending
        code points, nothing else."""
        for fn in _BOTH_SANITISERS:
            out = fn("café")
            self.assertIn("café", out)


if __name__ == "__main__":
    unittest.main()
