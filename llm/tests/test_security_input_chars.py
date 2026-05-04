"""
Defense-in-depth tests for control-character handling in user-supplied
string fields stored in the database.

Each test asserts a property the validator MUST hold.  A test that fails
before the fix is applied proves the gap is real; the same test passes
after the fix.

Findings covered
────────────────
DEV-01  LoginRequest.device_info accepts CRLF, null bytes, and other
        control characters.  Stored verbatim in Session.device_info.
        Not currently displayed anywhere — but a future audit-log or
        admin-console feature reading the field exposes log-injection
        and XSS shapes.  Same class of gap as EML-01 for email; close
        it here so the row in the database is always sanitary.

WEK-01  GameFinishRequest.weaknesses dict keys accept any string content
        of length ≤ 100.  Stored verbatim in GameEvent.weaknesses_json.
        Same defense-in-depth concern as DEV-01.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("SECA_ENV", "dev")

from pydantic import ValidationError

from llm.seca.auth.router import LoginRequest
from llm.seca.events.router import GameFinishRequest


_VALID_PGN = (
    '[Event "Test"]\n'
    '[Site "?"]\n'
    '[Date "????.??.??"]\n'
    '[Round "?"]\n'
    '[White "A"]\n'
    '[Black "B"]\n'
    '[Result "*"]\n\n'
    '1. e4 *'
)


# ---------------------------------------------------------------------------
# DEV-01  device_info control-character rejection
# ---------------------------------------------------------------------------

class TestDev01DeviceInfoControlChars(unittest.TestCase):
    """LoginRequest.device_info must reject control characters.

    The current validator only checks length (≤ 200).  Bytes 0x00–0x1f and
    0x7f have no legitimate place in a device-identifier string and must
    be rejected so the value persisted in Session.device_info is always
    safe to display, log, or re-emit elsewhere.
    """

    def test_rejects_newline(self):
        with self.assertRaises(ValidationError, msg="DEV-01: '\\n' accepted in device_info"):
            LoginRequest(email="a@b.co", password="p", device_info="x\nADMIN")

    def test_rejects_carriage_return(self):
        with self.assertRaises(ValidationError, msg="DEV-01: '\\r' accepted in device_info"):
            LoginRequest(email="a@b.co", password="p", device_info="x\rfake")

    def test_rejects_null_byte(self):
        with self.assertRaises(ValidationError, msg="DEV-01: null byte accepted in device_info"):
            LoginRequest(email="a@b.co", password="p", device_info="x\x00y")

    def test_rejects_tab(self):
        with self.assertRaises(ValidationError, msg="DEV-01: '\\t' accepted in device_info"):
            LoginRequest(email="a@b.co", password="p", device_info="x\ty")

    def test_rejects_other_control_chars(self):
        for c in ("\x01", "\x07", "\x1b", "\x7f"):
            with self.subTest(char=repr(c)):
                with self.assertRaises(ValidationError):
                    LoginRequest(email="a@b.co", password="p", device_info=f"x{c}y")

    def test_accepts_normal_device_strings(self):
        for value in ("", "Android API 33", "iPhone 15 Pro", "Pixel 8 (BP1A.250505)",
                      "register", "上海手机", "device-id_12345"):
            with self.subTest(device_info=value):
                try:
                    LoginRequest(email="a@b.co", password="p", device_info=value)
                except ValidationError as exc:
                    self.fail(f"DEV-01: legitimate device_info '{value!r}' rejected: {exc}")


# ---------------------------------------------------------------------------
# WEK-01  weakness keys control-character rejection
# ---------------------------------------------------------------------------

class TestWek01WeaknessKeyControlChars(unittest.TestCase):
    """GameFinishRequest.weaknesses keys must reject control characters.

    Defense-in-depth.  Keys are stored verbatim in GameEvent.weaknesses_json
    (JSON-encoded) and joined into log lines elsewhere; without character
    filtering an attacker could submit '\\n[ADMIN] log inject\\n' as a
    weakness name and pollute downstream consumers.  Numeric values are
    already type-checked; the gap is in the keys.
    """

    def test_rejects_newline_in_key(self):
        with self.assertRaises(ValidationError, msg="WEK-01: '\\n' accepted in weakness key"):
            GameFinishRequest(
                pgn=_VALID_PGN,
                result="win",
                accuracy=0.5,
                weaknesses={"tactics\nADMIN": 0.7},
            )

    def test_rejects_null_in_key(self):
        with self.assertRaises(ValidationError, msg="WEK-01: null byte accepted in weakness key"):
            GameFinishRequest(
                pgn=_VALID_PGN,
                result="win",
                accuracy=0.5,
                weaknesses={"x\x00y": 0.5},
            )

    def test_rejects_tab_in_key(self):
        with self.assertRaises(ValidationError, msg="WEK-01: '\\t' accepted in weakness key"):
            GameFinishRequest(
                pgn=_VALID_PGN,
                result="win",
                accuracy=0.5,
                weaknesses={"x\ty": 0.5},
            )

    def test_rejects_carriage_return_in_key(self):
        with self.assertRaises(ValidationError, msg="WEK-01: '\\r' accepted in weakness key"):
            GameFinishRequest(
                pgn=_VALID_PGN,
                result="win",
                accuracy=0.5,
                weaknesses={"x\ry": 0.5},
            )

    def test_accepts_normal_weakness_keys(self):
        normal = {
            "tactics": 0.7,
            "endgame": 0.4,
            "king-safety": 0.5,
            "pawn_structure": 0.3,
        }
        try:
            GameFinishRequest(
                pgn=_VALID_PGN, result="win", accuracy=0.5, weaknesses=normal,
            )
        except ValidationError as exc:
            self.fail(f"WEK-01: legitimate weakness keys rejected: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
