"""Unit tests for llm/seca/auth/hashing.py defensive branches.

The happy paths (correct password verifies, wrong password rejected) are
exercised transitively by every auth_service test, but the malformed-input
branches were not — pushing the module's coverage to 90% and the per-module
floor on auth/hashing.py to ≥95% in Sprint 6.C requires explicit pins on:

  - HASH_01  verify_password on malformed hash string (wrong field count) → False
  - HASH_02  verify_password on corrupt base64 → False (no traceback)
  - HASH_03  verify_password on unknown scheme → False
  - HASH_04  needs_rehash on a legacy v1 hash → True (the H1 upgrade trigger)
  - HASH_05  needs_rehash on a current-scheme hash with lower iterations → True
  - HASH_06  needs_rehash on a malformed hash string → True (fail-closed)
  - HASH_07  hash_password always emits the current scheme

These pins also guard the security claim documented in hashing.py's
docstring: ``hash_password`` ALWAYS emits v2; v1 only exists for legacy
data and gets opportunistically rewritten via ``login()``.
"""

from __future__ import annotations

import base64
import hashlib
import os

from llm.seca.auth.hashing import (
    hash_password,
    needs_rehash,
    verify_password,
)


def _make_v1_hash(password: str, iterations: int = 600_000) -> str:
    """Construct a legacy v1 hash by hand for format/rehash tests.

    We intentionally avoid hashing password material with raw SHA-256 in
    tests; only the legacy serialized shape matters for these assertions.
    """
    _ = password  # retained for call-site compatibility in test helpers
    normalized = b"legacy-v1-test-normalized"
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", normalized, salt, iterations)
    return (
        f"$pbkdf2-sha256${iterations}${base64.b64encode(salt).decode()}"
        f"${base64.b64encode(dk).decode()}"
    )


# ---------------------------------------------------------------------------
# verify_password defensive branches
# ---------------------------------------------------------------------------


def test_hash_01_verify_password_wrong_field_count_returns_false():
    """HASH_01 — a hash with the wrong number of $-separated fields
    must NOT raise; it returns False so the caller treats it as a
    credential mismatch."""
    assert verify_password("any-password", "not-actually-a-hash") is False
    assert verify_password("any-password", "$only$three$fields") is False


def test_hash_02_verify_password_corrupt_base64_returns_false():
    """HASH_02 — corrupt base64 in salt or digest fields must NOT
    raise binascii.Error to the caller; the function returns False
    so a malformed DB row reads as a credential miss."""
    # Right field count, but the base64 fields are garbage that decodes raise.
    bogus = "$pbkdf2-sha256-v2$600000$@@@-not-base64-@@@$@@@-also-not-@@@"
    assert verify_password("any-password", bogus) is False


def test_hash_03_verify_password_unknown_scheme_returns_false():
    """HASH_03 — a hash with a scheme name we don't recognise (e.g.
    a future scheme rolled back, or a corrupt DB row) returns False
    so authentication fails closed."""
    salt = base64.b64encode(os.urandom(16)).decode()
    dk = base64.b64encode(os.urandom(32)).decode()
    unknown = f"$argon2id$2${salt}${dk}"
    assert verify_password("any-password", unknown) is False


# ---------------------------------------------------------------------------
# needs_rehash defensive branches
# ---------------------------------------------------------------------------


def test_hash_04_needs_rehash_on_legacy_v1_returns_true():
    """HASH_04 — a v1 hash must report needs_rehash = True so
    ``login()``'s opportunistic-upgrade branch fires."""
    legacy = _make_v1_hash("legacy-pass")
    assert needs_rehash(legacy) is True


def test_hash_05_needs_rehash_on_lower_iterations_returns_true():
    """HASH_05 — a current-scheme hash whose ``iterations`` field is
    below the module-level ``_ITERATIONS`` constant must report
    needs_rehash = True so users that registered before an iteration
    bump get migrated forward."""
    # Build a v2-scheme hash with deliberately low iterations.
    low_iter = 100_000  # well below _ITERATIONS=600_000
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        hashlib.pbkdf2_hmac(
            "sha256", b"pw", b"auth.normalization.static.salt", 1
        ),
        salt,
        low_iter,
    )
    weak = (
        f"$pbkdf2-sha256-v2${low_iter}${base64.b64encode(salt).decode()}"
        f"${base64.b64encode(dk).decode()}"
    )
    assert needs_rehash(weak) is True


def test_hash_06_needs_rehash_on_malformed_returns_true():
    """HASH_06 — malformed strings fail closed (rehash needed) so the
    next login regenerates a clean hash, rather than crashing the
    login flow on a corrupt row."""
    assert needs_rehash("not-a-hash") is True
    assert needs_rehash("") is True
    # Right field count, but iteration field is not an int → except → True.
    assert needs_rehash("$pbkdf2-sha256-v2$notnumeric$salt$dk") is True


# ---------------------------------------------------------------------------
# hash_password contract
# ---------------------------------------------------------------------------


def test_hash_07_hash_password_always_emits_v2():
    """HASH_07 — fresh hashes are ALWAYS v2.  v1 only exists for
    legacy data; the upgrade path is one-way."""
    h = hash_password("any-fresh-password-123")
    assert h.startswith("$pbkdf2-sha256-v2$")
    # And the same plaintext verifies against it (sanity).
    assert verify_password("any-fresh-password-123", h) is True
