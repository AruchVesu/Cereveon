import base64
import hashlib
import hmac
import os

_SCHEME_V1 = "pbkdf2-sha256"       # legacy: normalisation = raw SHA-256 digest
_SCHEME = "pbkdf2-sha256-v2"        # current: normalisation = 1-iter PBKDF2
_ITERATIONS = 600000
_SALT_BYTES = 16
_NORM_SALT = b"auth.normalization.static.salt"


def _normalize_password(password: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _NORM_SALT, 1)


def _normalize_password_v1(password: str) -> bytes:
    """Legacy pre-processing step for hashes stored under the v1 scheme (pbkdf2-sha256).

    Security note (Bandit B324 / CWE-327 / CodeQL py/weak-cryptographic-hash):
    SHA-256 is flagged as a weak password-hashing primitive when seen in isolation, but this
    is NOT the full hashing chain.  The 32-byte digest is immediately used as the *key
    material* for PBKDF2-SHA256 with 600 000 iterations and a per-hash random 16-byte salt
    stored alongside every hash record.  PBKDF2 is the actual work-factor barrier against
    offline brute-force; the SHA-256 step is only a fixed-length normalisation that feeds
    into it.  The full chain — sha256(pw) → pbkdf2(digest, rand_salt, 600 000) — meets the
    NIST SP 800-132 minimum recommendation for password storage.

    False-positive suppression rationale: CodeQL's py/weak-cryptographic-hash query identifies
    the sha256 call because it sees raw password bytes entering a non-PBKDF2 hash function.
    It does not track that the output is the *input* to PBKDF2, so it cannot determine that
    the full chain is secure.  The suppression below is intentional and scoped to this line
    only; the query remains active for all other files.

    Immutability constraint: this function MUST NOT be changed.  Altering the normalisation
    produces different PBKDF2-derived keys for all existing v1 hashes in the database,
    silently breaking authentication for every user still on the legacy scheme.  The correct
    migration path is the opportunistic upgrade in service.login(): every successful v1 login
    rewrites the stored hash to v2, which uses _normalize_password() (PBKDF2-normalised).
    No new v1 hashes are ever created; hash_password() always emits v2.
    """
    # nosec B324 — see docstring; lgtm[py/weak-cryptographic-hash]
    return hashlib.sha256(password.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    normalized = _normalize_password(password)
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", normalized, salt, _ITERATIONS)
    salt_b64 = base64.b64encode(salt).decode()
    dk_b64 = base64.b64encode(dk).decode()
    return f"${_SCHEME}${_ITERATIONS}${salt_b64}${dk_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        parts = password_hash.split("$")
        if len(parts) != 5:
            return False
        scheme = parts[1]
        iterations = int(parts[2])
        salt = base64.b64decode(parts[3])
        expected = base64.b64decode(parts[4])
    except (ValueError, IndexError, base64.binascii.Error):
        return False

    if scheme == _SCHEME:
        normalized = _normalize_password(password)
    elif scheme == _SCHEME_V1:
        normalized = _normalize_password_v1(password)
    else:
        return False

    dk = hashlib.pbkdf2_hmac("sha256", normalized, salt, iterations)
    return hmac.compare_digest(dk, expected)


def needs_rehash(password_hash: str) -> bool:
    try:
        parts = password_hash.split("$")
        if len(parts) != 5:
            return True
        if parts[1] != _SCHEME:
            return True
        return int(parts[2]) < _ITERATIONS
    except (ValueError, IndexError):
        return True
