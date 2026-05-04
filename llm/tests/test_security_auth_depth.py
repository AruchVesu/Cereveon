"""
Objective security tests for authentication-layer hardening gaps.

Each test asserts what the validator/service SHOULD enforce.  Tests that
fail before the fix is applied prove the vulnerability objectively; the
same tests pass after the fix.

Findings covered
────────────────
TIM-01  service.login() leaks user existence via PBKDF2 timing
        (≈270 ms gap between non-existent and existing emails — 955x ratio)

EML-01  RegisterRequest.email accepts CRLF and control characters
        (log-injection vector if email is ever passed to a logger)

EML-02  LoginRequest.email has no validator at all
        (same surface as EML-01, plus null bytes and 320-char garbage)

EML-03  Email validator is structurally too permissive
        (accepts '@@@', 'a@b', 'x@\\x00@y' — no proper RFC-shape check)
"""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("SECA_ENV", "dev")

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.seca.auth.models import Base
from llm.seca.auth.router import LoginRequest, RegisterRequest
from llm.seca.auth.service import AuthService


def _fresh_db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng)()


# ---------------------------------------------------------------------------
# TIM-01  Email-enumeration timing oracle in service.login()
# ---------------------------------------------------------------------------

class TestTim01LoginTimingOracle(unittest.TestCase):
    """service.login() must NOT reveal user existence through timing.

    The current implementation skips PBKDF2 verification when the email is
    not in the database (~0.3 ms) but performs full PBKDF2 (~270 ms) for an
    existing email with a wrong password.  An attacker can enumerate registered
    emails by measuring response time alone.

    Fix: perform a dummy verify_password() call against a precomputed hash
    when the player lookup misses, so both paths take the same wall time.
    """

    @classmethod
    def setUpClass(cls):
        cls.db = _fresh_db()
        cls.svc = AuthService(cls.db)
        cls.svc.register("exists@example.com", "correctpassword")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def _time_login(self, email: str, password: str, n: int = 5) -> float:
        # warm up once so module import / first PBKDF2 cost is amortised
        try:
            self.svc.login(email, password)
        except ValueError:
            pass
        start = time.perf_counter()
        for _ in range(n):
            try:
                self.svc.login(email, password)
            except ValueError:
                pass
        return (time.perf_counter() - start) / n

    def test_login_timing_for_nonexistent_and_existing_emails_is_close(self):
        """Both code paths must take comparable wall time (< 3x ratio).

        A wrong password against an existing account should not cost
        meaningfully more than a wrong password against a non-existent
        account.  The 3x bound is generous — current code shows ~955x.
        """
        t_nonexist = self._time_login("nonexist@example.com", "wrongpassword")
        t_exists = self._time_login("exists@example.com", "wrongpassword")

        # Use the slower of the two as the floor to avoid divide-by-zero edge
        slower = max(t_nonexist, t_exists)
        faster = min(t_nonexist, t_exists)
        ratio = slower / max(faster, 1e-9)

        self.assertLess(
            ratio,
            3.0,
            f"TIM-01: login timing differs by {ratio:.1f}x "
            f"(nonexist={t_nonexist*1000:.1f} ms vs exists={t_exists*1000:.1f} ms) — "
            f"this is an email-enumeration oracle.  Run a dummy verify_password() "
            f"on the miss path to equalise timing.",
        )


# ---------------------------------------------------------------------------
# EML-01  RegisterRequest.email rejects CRLF / control chars
# ---------------------------------------------------------------------------

class TestEml01RegisterRequestEmailControlChars(unittest.TestCase):
    """RegisterRequest.email must reject CRLF and control characters.

    Current validator only checks length and the presence of '@'.  Strings
    like 'victim@x.com\\nADMIN: true' pass, opening a log-injection vector
    if the email is ever passed to a structured logger or audit trail.
    """

    def test_rejects_newline(self):
        with self.assertRaises(ValidationError, msg="EML-01: '\\n' in email accepted"):
            RegisterRequest(email="victim@x.com\nADMIN", password="validpassword")

    def test_rejects_carriage_return(self):
        with self.assertRaises(ValidationError, msg="EML-01: '\\r' in email accepted"):
            RegisterRequest(email="victim@x.com\rfake", password="validpassword")

    def test_rejects_tab(self):
        with self.assertRaises(ValidationError, msg="EML-01: '\\t' in email accepted"):
            RegisterRequest(email="victim@x.com\tfake", password="validpassword")

    def test_rejects_null_byte(self):
        with self.assertRaises(ValidationError, msg="EML-01: null byte in email accepted"):
            RegisterRequest(email="victim\x00@x.com", password="validpassword")

    def test_rejects_other_control_chars(self):
        for c in ("\x01", "\x07", "\x1b", "\x7f"):
            with self.subTest(char=repr(c)):
                with self.assertRaises(ValidationError):
                    RegisterRequest(email=f"victim{c}@x.com", password="validpassword")

    def test_accepts_normal_email(self):
        try:
            RegisterRequest(email="user@example.com", password="validpassword")
        except ValidationError as exc:
            self.fail(f"EML-01: valid email rejected: {exc}")


# ---------------------------------------------------------------------------
# EML-02  LoginRequest.email has no validator
# ---------------------------------------------------------------------------

class TestEml02LoginRequestEmailValidation(unittest.TestCase):
    """LoginRequest.email must apply the same validation as RegisterRequest.email.

    Currently LoginRequest validates password and device_info but accepts ANY
    string for email — including binary garbage and CRLF.  The login flow
    queries the database with this string, and any logging in that path is
    exposed to log injection."""

    def test_login_rejects_newline_in_email(self):
        with self.assertRaises(ValidationError, msg="EML-02: '\\n' in login email accepted"):
            LoginRequest(email="victim@x.com\nADMIN", password="p")

    def test_login_rejects_null_byte_in_email(self):
        with self.assertRaises(ValidationError, msg="EML-02: null byte in login email accepted"):
            LoginRequest(email="x\x00y@z.com", password="p")

    def test_login_rejects_overlong_email(self):
        with self.assertRaises(ValidationError, msg="EML-02: 400-char login email accepted"):
            LoginRequest(email="a" * 400 + "@x.com", password="p")

    def test_login_accepts_normal_email(self):
        try:
            LoginRequest(email="user@example.com", password="p")
        except ValidationError as exc:
            self.fail(f"EML-02: valid login email rejected: {exc}")


# ---------------------------------------------------------------------------
# EML-03  Email validator structurally too permissive
# ---------------------------------------------------------------------------

class TestEml03EmailFormatStrictness(unittest.TestCase):
    """The email validator must reject obviously malformed addresses.

    Examples that the current validator accepts but should be rejected:
        '@@@'       — three '@' characters with no local or domain part
        'a@b'       — no TLD
        '@x.com'    — empty local part
        'user@'     — empty domain
        '<script>@evil.com' — angle brackets (XSS payload shape)
    """

    cases_to_reject = [
        "@@@",
        "a@b",            # no TLD
        "@x.com",         # empty local part
        "user@",          # empty domain
        "user @example.com",  # internal whitespace
        "<script>@evil.com",  # angle brackets
    ]

    def test_register_rejects_malformed(self):
        for bad in self.cases_to_reject:
            with self.subTest(email=bad):
                with self.assertRaises(ValidationError,
                                       msg=f"EML-03: malformed '{bad}' accepted in RegisterRequest"):
                    RegisterRequest(email=bad, password="validpassword")

    def test_login_rejects_malformed(self):
        for bad in self.cases_to_reject:
            with self.subTest(email=bad):
                with self.assertRaises(ValidationError,
                                       msg=f"EML-03: malformed '{bad}' accepted in LoginRequest"):
                    LoginRequest(email=bad, password="p")

    def test_register_accepts_well_formed(self):
        well_formed = [
            "user@example.com",
            "first.last@example.co.uk",
            "user+tag@sub.example.com",
            "x@y.io",
        ]
        for email in well_formed:
            with self.subTest(email=email):
                try:
                    RegisterRequest(email=email, password="validpassword")
                except ValidationError as exc:
                    self.fail(f"EML-03: well-formed email '{email}' rejected: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
