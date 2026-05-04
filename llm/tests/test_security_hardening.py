"""
Security hardening tests — llm/tests/test_security_hardening.py

Covers security properties not yet pinned by test_api_security.py.
All tests are CI-safe: no live Stockfish, no real HTTP server, no network I/O.

Stable test IDs (do NOT rename):
  SH_01  PBKDF2 iteration count >= 600 000
  SH_02  Same password produces different hashes (random salt enforced)
  SH_03  verify_password uses hmac.compare_digest (timing-safe)
  SH_04  verify_password rejects malformed/truncated hash strings
  SH_05  verify_password rejects unrecognised scheme names
  SH_05b verify_password accepts the correct password
  SH_05c verify_password rejects an incorrect password
  SH_06  Expired JWT token rejected by decode_token
  SH_07  JWT signed with wrong secret key rejected
  SH_08  JWT alg:none attack rejected
  SH_09  JWT algorithm is pinned to HS256
  SH_09b SECRET_KEY length guard in tokens.py source (< 32 chars raises)
  SH_10  get_player_by_session rejects DB-expired session (defence-in-depth)
  SH_10b get_player_by_session accepts a valid (unexpired) session
  SH_11  Body size middleware: Content-Length > 512 KB returns 413
  SH_11b Body size middleware: Content-Length <= 512 KB accepted
  SH_12  Security response headers present on every response
  SH_12b X-Frame-Options is DENY
  SH_12c Security headers configured in server.py source
  SH_13  change_password rejects new password shorter than 8 characters
  SH_13b change_password rejects wrong current password
  SH_13c change_password accepts a valid change
  SH_14  check_db.py iterates over hardcoded allowlist (no dynamic table names)
  SH_15  repo.py contains no f-string SQL interpolation
  SH_15b event_store.py contains no f-string SQL interpolation
  SH_16  seca_doctor.py defines _ALLOWED_TABLES before any SQL execution
  SH_16b seca_doctor.py references _ALLOWED_TABLES in its body
  SH_17  /auth/register has @limiter.limit rate-limiting decorator
  SH_17b /auth/login has @limiter.limit rate-limiting decorator
  SH_18  server.py guards against missing SECA_API_KEY in production
  SH_18b tokens.py guards against missing/short SECRET_KEY in production
  SH_18c server.py derives IS_PROD from SECA_ENV value comparison
  SH_19  CORS_ALLOWED_ORIGINS does not default to wildcard '*'
  SH_19b server.py logs a warning when CORS origins are not configured
  SH_20  Session model has expires_at column
  SH_20b Session model expires_at has a non-null default
  SH_20c service.py get_player_by_session references expires_at
  SH_21  pytest in requirements-ci.txt is pinned at >= 8.1.0 (GHSA-w234-x5rp-h73c)
  SH_22  v1 hashes verify correctly (PBKDF2 bottleneck maintained despite SHA-256 pre-step)
  SH_23  v1 hashes always trigger needs_rehash() — auto-upgrade path is mandatory
  SH_24  hash_password() never emits a v1-scheme hash — no new v1 hashes created
  SH_25  service.py login contains opportunistic v1→v2 upgrade (source inspection)
"""

from __future__ import annotations

import ast
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LLM = _REPO_ROOT / "llm"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(relative: str) -> str:
    return (_LLM / relative).read_text(encoding="utf-8")


def _parse(relative: str) -> ast.Module:
    return ast.parse(_read(relative))


# ===========================================================================
# SH_01 – SH_05c — Password Hashing
# ===========================================================================


class TestPasswordHashing:
    """Unit tests for PBKDF2 hashing in llm/seca/auth/hashing.py."""

    def setup_method(self):
        from llm.seca.auth.hashing import (  # noqa: PLC0415
            hash_password,
            verify_password,
            _ITERATIONS,
            _SCHEME,
        )

        self.hash_password = hash_password
        self.verify_password = verify_password
        self.iterations = _ITERATIONS
        self.scheme = _SCHEME

    def test_sh01_iteration_count_at_least_600k(self):
        """SH_01: PBKDF2 iteration count must be >= 600 000 (OWASP 2023 baseline)."""
        assert self.iterations >= 600_000, (
            f"PBKDF2 iterations is {self.iterations} — must be >= 600 000 per OWASP 2023. "
            "Reducing this weakens resistance to offline brute-force attacks."
        )

    def test_sh02_unique_salt_per_hash(self):
        """SH_02: Same password must produce different hashes (random per-password salt)."""
        pw = "test_password_uniqueness_abc123"
        h1 = self.hash_password(pw)
        h2 = self.hash_password(pw)
        assert h1 != h2, (
            "hash_password returned the same hash for the same password twice. "
            "A static salt is in use — random per-password salts are required."
        )

    def test_sh03_timing_safe_comparison_in_source(self):
        """SH_03: verify_password must use hmac.compare_digest (constant-time comparison)."""
        source = _read("seca/auth/hashing.py")
        assert "hmac.compare_digest" in source, (
            "verify_password does not use hmac.compare_digest. "
            "Using == for hash comparison is vulnerable to timing-based side-channel attacks."
        )

    def test_sh04_malformed_hash_rejected(self):
        """SH_04: verify_password must return False for truncated or malformed hash strings."""
        for bad in ["", "not-a-hash", "$only-one-part", "x$y"]:
            assert self.verify_password("anypassword", bad) is False, (
                f"verify_password returned True for malformed hash {bad!r}. "
                "Must return False to prevent authentication bypass."
            )

    def test_sh05_wrong_scheme_rejected(self):
        """SH_05: verify_password must reject hashes that use an unrecognised scheme name."""
        import base64
        import hashlib

        salt = b"\x00" * 16
        dk = hashlib.pbkdf2_hmac("sha256", b"test", salt, 1)
        fake = (
            f"$bogus-scheme$1"
            f"${base64.b64encode(salt).decode()}"
            f"${base64.b64encode(dk).decode()}"
        )
        assert not self.verify_password("test", fake), (
            "verify_password accepted a hash with an unrecognised scheme — must reject."
        )

    def test_sh05b_correct_password_accepted(self):
        """SH_05b: verify_password must accept the password used to create the hash."""
        pw = "correct_horse_battery_staple_!7"
        stored = self.hash_password(pw)
        assert self.verify_password(pw, stored), "verify_password rejected the correct password."

    def test_sh05c_wrong_password_rejected(self):
        """SH_05c: verify_password must reject a password that differs from the stored one."""
        pw = "correct_horse_battery_staple_!7"
        stored = self.hash_password(pw)
        assert not self.verify_password("wrong_password_xyz", stored), (
            "verify_password accepted an incorrect password."
        )


# ===========================================================================
# SH_06 – SH_09b — JWT / Token Security
# ===========================================================================


class TestJWTSecurity:
    """Unit tests for JWT handling in llm/seca/auth/tokens.py."""

    def setup_method(self):
        os.environ.setdefault("SECA_ENV", "dev")
        from llm.seca.auth.tokens import (  # noqa: PLC0415
            ALGORITHM,
            SECRET_KEY,
            create_access_token,
            decode_token,
        )

        self.create_token = create_access_token
        self.decode_token = decode_token
        self.algorithm = ALGORITHM
        self.secret = SECRET_KEY

    def test_sh06_expired_token_rejected(self):
        """SH_06: An already-expired JWT must be rejected by decode_token."""
        import jwt as _jwt

        expired_payload = {
            "player_id": "test-player",
            "session_id": "test-session",
            "exp": datetime.utcnow() - timedelta(seconds=5),
        }
        expired_token = _jwt.encode(expired_payload, self.secret, algorithm=self.algorithm)
        with pytest.raises(Exception, match="(?i)(expired|decode|signature)"):
            self.decode_token(expired_token)

    def test_sh07_wrong_secret_rejected(self):
        """SH_07: A JWT signed with a different secret must be rejected."""
        import jwt as _jwt

        payload = {
            "player_id": "test",
            "session_id": "test",
            "exp": datetime.utcnow() + timedelta(minutes=5),
        }
        bad_token = _jwt.encode(payload, "wrong_secret_key_wrong_secret_key!!", algorithm=self.algorithm)
        with pytest.raises(Exception):
            self.decode_token(bad_token)

    def test_sh08_alg_none_rejected(self):
        """SH_08: A JWT using alg:none (unsigned) must be rejected."""
        import base64
        import json

        header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        body_data = {
            "player_id": "attacker",
            "session_id": "fake",
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
        }
        body = base64.urlsafe_b64encode(json.dumps(body_data).encode()).rstrip(b"=").decode()
        none_token = f"{header}.{body}."

        with pytest.raises(Exception):
            self.decode_token(none_token)

    def test_sh09_algorithm_is_hs256(self):
        """SH_09: Token signing must use HS256 algorithm (symmetric, auditable)."""
        assert self.algorithm == "HS256", (
            f"JWT algorithm is {self.algorithm!r} — expected HS256. "
            "Changing the algorithm can introduce algorithm-confusion vulnerabilities."
        )

    def test_sh09b_secret_key_length_guard_in_source(self):
        """SH_09b: tokens.py source must contain a RuntimeError guard for short SECRET_KEY."""
        source = _read("seca/auth/tokens.py")
        assert "len(SECRET_KEY) < 32" in source, (
            "tokens.py does not guard against SHORT SECRET_KEY values. "
            "A key shorter than 32 characters weakens HMAC-SHA256 security."
        )
        assert "raise RuntimeError" in source, (
            "tokens.py does not raise RuntimeError when SECRET_KEY constraints are violated."
        )


# ===========================================================================
# SH_10 – SH_10b — Session Expiry (defence-in-depth)
# ===========================================================================


class TestSessionExpiry:
    """Verify that get_player_by_session enforces DB-level session expiry."""

    def _make_db(self):
        from sqlalchemy import create_engine  # noqa: PLC0415
        from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

        from llm.seca.auth.models import Base  # noqa: PLC0415

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        LocalSession = sessionmaker(bind=engine)
        return LocalSession()

    def test_sh10_expired_session_rejected(self):
        """SH_10: get_player_by_session must reject sessions whose expires_at is in the past."""
        import hashlib
        import uuid

        from llm.seca.auth.hashing import hash_password  # noqa: PLC0415
        from llm.seca.auth.models import Player  # noqa: PLC0415
        from llm.seca.auth.models import Session as AuthSession  # noqa: PLC0415
        from llm.seca.auth.service import AuthService  # noqa: PLC0415
        from llm.seca.auth.tokens import create_access_token  # noqa: PLC0415

        db = self._make_db()

        player = Player(
            id=str(uuid.uuid4()),
            email="expired@test.com",
            password_hash=hash_password("TestPass123!"),
        )
        db.add(player)
        db.commit()

        session_id = str(uuid.uuid4())
        token = create_access_token(player_id=str(player.id), session_id=session_id)
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        expired_session = AuthSession(
            id=session_id,
            player_id=player.id,
            token_hash=token_hash,
            expires_at=datetime.utcnow() - timedelta(days=1),  # already expired
        )
        db.add(expired_session)
        db.commit()

        service = AuthService(db)
        result = service.get_player_by_session(session_id, token)
        assert result is None, (
            "get_player_by_session returned a player for a DB-expired session. "
            "Session expiry (expires_at) must be checked as defence-in-depth."
        )
        db.close()

    def test_sh10b_valid_session_accepted(self):
        """SH_10b: get_player_by_session must accept a session whose expires_at is in the future."""
        import hashlib
        import uuid

        from llm.seca.auth.hashing import hash_password  # noqa: PLC0415
        from llm.seca.auth.models import Player  # noqa: PLC0415
        from llm.seca.auth.models import Session as AuthSession  # noqa: PLC0415
        from llm.seca.auth.service import AuthService  # noqa: PLC0415
        from llm.seca.auth.tokens import create_access_token  # noqa: PLC0415

        db = self._make_db()

        player = Player(
            id=str(uuid.uuid4()),
            email="valid@test.com",
            password_hash=hash_password("ValidPass123!"),
        )
        db.add(player)
        db.commit()

        session_id = str(uuid.uuid4())
        token = create_access_token(player_id=str(player.id), session_id=session_id)
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        valid_session = AuthSession(
            id=session_id,
            player_id=player.id,
            token_hash=token_hash,
            expires_at=datetime.utcnow() + timedelta(days=7),  # still valid
        )
        db.add(valid_session)
        db.commit()

        service = AuthService(db)
        result = service.get_player_by_session(session_id, token)
        assert result is not None, "get_player_by_session rejected a valid (unexpired) session."
        db.close()


# ===========================================================================
# SH_11 – SH_12c — Middleware (stub app)
# ===========================================================================


def _make_middleware_app():
    """Minimal FastAPI app that reproduces server.py middleware under test."""
    from fastapi import FastAPI, Request  # noqa: PLC0415
    from fastapi.responses import JSONResponse  # noqa: PLC0415
    from starlette.middleware.base import BaseHTTPMiddleware  # noqa: PLC0415

    stub = FastAPI()
    _MAX_BODY = 512 * 1024

    class _LimitBody(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            cl = request.headers.get("content-length")
            if cl:
                try:
                    if int(cl) > _MAX_BODY:
                        return JSONResponse(status_code=413, content={"error": "Request body too large"})
                except ValueError:
                    return JSONResponse(status_code=400, content={"error": "Invalid Content-Length"})
            return await call_next(request)

    stub.add_middleware(_LimitBody)

    @stub.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @stub.get("/ping")
    def ping():
        return {"ok": True}

    return stub


class TestBodySizeLimit:
    """Body size limit middleware."""

    def test_sh11_oversized_content_length_returns_413(self):
        """SH_11: Content-Length > 512 KB must return HTTP 413."""
        from fastapi.testclient import TestClient  # noqa: PLC0415

        client = TestClient(_make_middleware_app(), raise_server_exceptions=False)
        oversized = str(512 * 1024 + 1)
        resp = client.get("/ping", headers={"Content-Length": oversized})
        assert resp.status_code == 413, (
            f"Expected 413 for Content-Length={oversized}, got {resp.status_code}. "
            "The body size limit middleware is not enforcing the 512 KB cap."
        )

    def test_sh11b_normal_content_length_accepted(self):
        """SH_11b: Content-Length <= 512 KB must not be blocked."""
        from fastapi.testclient import TestClient  # noqa: PLC0415

        client = TestClient(_make_middleware_app())
        resp = client.get("/ping", headers={"Content-Length": "1024"})
        assert resp.status_code == 200


class TestSecurityHeaders:
    """Security response headers middleware."""

    def _get(self):
        from fastapi.testclient import TestClient  # noqa: PLC0415

        return TestClient(_make_middleware_app()).get("/ping")

    def test_sh12_all_security_headers_present(self):
        """SH_12: Responses must include all required security headers."""
        resp = self._get()
        required = [
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
        ]
        missing = [h for h in required if h not in resp.headers]
        assert not missing, f"Missing security headers: {missing}"

    def test_sh12b_x_frame_options_is_deny(self):
        """SH_12b: X-Frame-Options must be DENY to prevent clickjacking."""
        resp = self._get()
        assert resp.headers.get("X-Frame-Options") == "DENY", (
            f"X-Frame-Options is {resp.headers.get('X-Frame-Options')!r} — must be 'DENY'."
        )

    def test_sh12c_security_headers_in_server_source(self):
        """SH_12c: server.py must configure all required security headers in middleware."""
        source = _read("server.py")
        required = [
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
        ]
        missing = [h for h in required if h not in source]
        assert not missing, (
            f"Security headers missing from server.py middleware: {missing}. "
            "Every response must carry these headers."
        )


# ===========================================================================
# SH_13 – SH_13c — Password Change Validation
# ===========================================================================


class TestChangePasswordValidation:
    """AuthService.change_password password-policy enforcement."""

    def _setup(self):
        import uuid  # noqa: PLC0415

        from sqlalchemy import create_engine  # noqa: PLC0415
        from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

        from llm.seca.auth.hashing import hash_password  # noqa: PLC0415
        from llm.seca.auth.models import Base, Player  # noqa: PLC0415
        from llm.seca.auth.service import AuthService  # noqa: PLC0415

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        LocalSession = sessionmaker(bind=engine)
        db = LocalSession()

        player = Player(
            id=str(uuid.uuid4()),
            email="change@test.com",
            password_hash=hash_password("OldPass12345!"),
        )
        db.add(player)
        db.commit()
        db.refresh(player)
        return db, player, AuthService(db)

    def test_sh13_short_new_password_rejected(self):
        """SH_13: change_password must reject new passwords shorter than 8 characters."""
        db, player, svc = self._setup()
        with pytest.raises(ValueError, match="8"):
            svc.change_password(player, "OldPass12345!", "short")
        db.close()

    def test_sh13b_wrong_current_password_rejected(self):
        """SH_13b: change_password must reject if current_password is incorrect."""
        db, player, svc = self._setup()
        with pytest.raises(ValueError):
            svc.change_password(player, "WrongCurrentPassword!", "NewLongPassword123!")
        db.close()

    def test_sh13c_valid_change_accepted(self):
        """SH_13c: change_password must accept and store a valid new password."""
        from llm.seca.auth.hashing import verify_password  # noqa: PLC0415

        db, player, svc = self._setup()
        svc.change_password(player, "OldPass12345!", "NewPassword1234!")
        assert verify_password("NewPassword1234!", player.password_hash), (
            "Password was not updated correctly by change_password."
        )
        db.close()


# ===========================================================================
# SH_14 – SH_16b — SQL Safety (source inspection)
# ===========================================================================


class TestSQLSafety:
    """Verify that production code paths use parameterised SQL, not f-string interpolation."""

    _SQL_KW = re.compile(r"(?:SELECT|INSERT|UPDATE|DELETE)", re.IGNORECASE)
    _FSTRING_SQL = re.compile(
        r'f["\'](?:[^"\']*?)(?:SELECT|INSERT|UPDATE|DELETE)(?:[^"\']*?)\{[^}]+\}(?:[^"\']*?)["\']',
        re.IGNORECASE | re.DOTALL,
    )

    def test_sh14_check_db_uses_allowlist(self):
        """SH_14: check_db.py must iterate over a hardcoded table allowlist."""
        source = _read("check_db.py")
        has_allowlist = (
            "_ALLOWED_TABLES" in source
            or bool(re.search(r'tables\s*=\s*[\[{(]', source))
        )
        assert has_allowlist, (
            "check_db.py does not define a hardcoded table allowlist. "
            "Table names used in dynamic SQL must come from a vetted frozenset or list literal."
        )

    def test_sh15_repo_py_no_fstring_sql(self):
        """SH_15: repo.py must not interpolate variables into SQL via f-strings."""
        source = _read("seca/storage/repo.py")
        hits = self._FSTRING_SQL.findall(source)
        assert not hits, (
            f"repo.py contains f-string SQL interpolation: {hits}. "
            "Use parameterised queries (?) to prevent SQL injection."
        )

    def test_sh15b_event_store_no_fstring_sql(self):
        """SH_15b: event_store.py must not interpolate variables into SQL via f-strings."""
        source = _read("seca/storage/event_store.py")
        hits = self._FSTRING_SQL.findall(source)
        assert not hits, f"event_store.py contains f-string SQL: {hits}"

    def test_sh15c_db_py_no_fstring_sql(self):
        """SH_15c: storage/db.py must not interpolate variables into SQL via f-strings."""
        source = _read("seca/storage/db.py")
        hits = self._FSTRING_SQL.findall(source)
        assert not hits, f"storage/db.py contains f-string SQL: {hits}"

    def test_sh16_seca_doctor_defines_allowed_tables_before_sql(self):
        """SH_16: seca_doctor.py must define _ALLOWED_TABLES before any cur.execute call."""
        source = _read("seca/seca_doctor.py")
        assert "_ALLOWED_TABLES" in source, (
            "seca_doctor.py does not define _ALLOWED_TABLES. "
            "All table names used in dynamic SQL must be validated against an allowlist."
        )
        allowed_pos = source.find("_ALLOWED_TABLES")
        execute_pos = source.find("cur.execute")
        assert allowed_pos != -1 and execute_pos != -1, (
            "seca_doctor.py is missing either _ALLOWED_TABLES or cur.execute."
        )
        assert allowed_pos < execute_pos, (
            "_ALLOWED_TABLES is referenced after cur.execute in seca_doctor.py. "
            "The allowlist must appear before any SQL execution."
        )

    def test_sh16b_seca_doctor_nosec_or_allowlist_check_present(self):
        """SH_16b: seca_doctor.py execute call must carry nosec or reference the allowlist."""
        source = _read("seca/seca_doctor.py")
        # Either a nosec annotation or an explicit `in _ALLOWED_TABLES` guard
        has_nosec = "nosec" in source
        has_guard = re.search(r"(not in|in)\s+_ALLOWED_TABLES", source)
        assert has_nosec or has_guard, (
            "seca_doctor.py cur.execute call has neither a '# nosec' annotation "
            "nor an explicit 'in _ALLOWED_TABLES' guard. Add one to document intent."
        )


# ===========================================================================
# SH_17 – SH_17b — Rate Limiting Decorators (AST)
# ===========================================================================


def _has_limiter_decorator(func_def: ast.FunctionDef) -> bool:
    for dec in func_def.decorator_list:
        if isinstance(dec, ast.Call):
            f = dec.func
            if isinstance(f, ast.Attribute) and f.attr == "limit":
                return True
    return False


class TestRateLimitingDecorators:
    """Verify that auth endpoints carry @limiter.limit decorators."""

    def setup_method(self):
        tree = _parse("seca/auth/router.py")
        self._funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def test_sh17_register_has_rate_limit(self):
        """SH_17: /auth/register must have a @limiter.limit rate-limiting decorator."""
        func = self._funcs.get("register")
        assert func is not None, "register() not found in auth/router.py"
        assert _has_limiter_decorator(func), (
            "/auth/register has no @limiter.limit decorator. "
            "Without rate limiting the endpoint is vulnerable to account enumeration and DoS."
        )

    def test_sh17b_login_has_rate_limit(self):
        """SH_17b: /auth/login must have a @limiter.limit rate-limiting decorator."""
        func = self._funcs.get("login")
        assert func is not None, "login() not found in auth/router.py"
        assert _has_limiter_decorator(func), (
            "/auth/login has no @limiter.limit decorator. "
            "Without rate limiting the endpoint is vulnerable to password brute-force."
        )


# ===========================================================================
# SH_18 – SH_18c — Production Guards (source inspection)
# ===========================================================================


class TestProductionGuards:
    """Verify that production-mode safety guards are present in source."""

    def test_sh18_server_guards_api_key_in_prod(self):
        """SH_18: server.py must raise RuntimeError when SECA_API_KEY is absent in production."""
        source = _read("server.py")
        assert "IS_PROD" in source, "server.py does not define IS_PROD flag."
        assert "raise RuntimeError" in source, (
            "server.py does not raise RuntimeError for production misconfiguration. "
            "A missing SECA_API_KEY in production must abort startup."
        )

    def test_sh18b_tokens_guards_secret_key_in_prod(self):
        """SH_18b: tokens.py must raise RuntimeError when SECRET_KEY is absent/short in production."""
        source = _read("seca/auth/tokens.py")
        assert "raise RuntimeError" in source, (
            "tokens.py does not raise RuntimeError when SECRET_KEY constraints are violated. "
            "Starting without a stable signing secret in production is a critical misconfiguration."
        )

    def test_sh18c_is_prod_derived_from_seca_env_value(self):
        """SH_18c: IS_PROD in server.py must compare SECA_ENV to a string, not just test truthiness."""
        source = _read("server.py")
        # Must have IS_PROD = ENV in {"prod", "production"} or equivalent comparison
        assert re.search(r'IS_PROD\s*=.*ENV', source), (
            "server.py IS_PROD is not derived from a SECA_ENV string comparison. "
            "Truthiness checks on env vars can be bypassed with any non-empty value."
        )


# ===========================================================================
# SH_19 – SH_19b — CORS Configuration
# ===========================================================================


class TestCORSConfiguration:
    """Verify that CORS is not misconfigured to allow wildcard origins."""

    def test_sh19_cors_no_wildcard_default(self):
        """SH_19: CORS_ALLOWED_ORIGINS must not default to '*' in server.py."""
        source = _read("server.py")
        cors_lines = [
            line for line in source.splitlines()
            if "CORS_ALLOWED_ORIGINS" in line and "getenv" in line
        ]
        assert cors_lines, "CORS_ALLOWED_ORIGINS not configured via os.getenv in server.py."
        for line in cors_lines:
            assert '"*"' not in line and "'*'" not in line, (
                f"CORS_ALLOWED_ORIGINS defaults to wildcard '*': {line.strip()!r}. "
                "A wildcard CORS policy allows any origin to make credentialed requests."
            )

    def test_sh19b_cors_misconfig_is_never_silent(self):
        """SH_19b: server.py must surface an empty CORS_ALLOWED_ORIGINS loudly.

        Behaviour is split by environment so both dev ergonomics and prod
        safety are preserved:

          - In production (``IS_PROD`` true) an empty CORS_ALLOWED_ORIGINS
            must cause a hard ``RuntimeError`` at startup, mirroring the
            existing SECA_API_KEY / SECRET_KEY pattern.  A misconfigured
            production deployment fails loud at boot rather than silently
            blocking every browser.

          - In development an empty CORS_ALLOWED_ORIGINS must emit at
            least a logger.* line that mentions the env var, so a
            contributor running locally without the var can see that the
            dev-default fallback is in effect rather than wondering why
            cross-origin requests work.

        Either path is acceptable; what is not acceptable is silent
        misconfiguration.
        """
        source = _read("server.py")
        assert "CORS_ALLOWED_ORIGINS" in source, (
            "CORS_ALLOWED_ORIGINS env var is not referenced in server.py at all."
        )
        # Prod hard-fail must mention CORS_ALLOWED_ORIGINS in its RuntimeError
        # message — searched as one block so newlines between IS_PROD branch
        # and raise RuntimeError do not defeat the regex.
        has_prod_hard_fail = bool(
            re.search(
                r"IS_PROD[\s\S]{0,200}raise RuntimeError[\s\S]{0,200}CORS_ALLOWED_ORIGINS"
                r"|CORS_ALLOWED_ORIGINS[\s\S]{0,200}raise RuntimeError[\s\S]{0,200}IS_PROD"
                r"|raise RuntimeError\([\s\S]{0,300}CORS_ALLOWED_ORIGINS",
                source,
            )
        )
        # Dev-mode log line must reference the env var.  Either logger.info or
        # logger.warning is acceptable — the point is visibility, not severity.
        has_dev_log = bool(
            re.search(
                r"logger\.(info|warning)[\s\S]{0,300}CORS_ALLOWED_ORIGINS"
                r"|CORS_ALLOWED_ORIGINS[\s\S]{0,300}logger\.(info|warning)",
                source,
            )
        )
        assert has_prod_hard_fail, (
            "server.py does not raise RuntimeError on empty CORS_ALLOWED_ORIGINS "
            "in production.  Silent failure in prod is unacceptable."
        )
        assert has_dev_log, (
            "server.py does not log when CORS_ALLOWED_ORIGINS is empty in dev. "
            "Contributors must see the dev-default fallback rather than guess."
        )


# ===========================================================================
# SH_20 – SH_20c — Session Model Expiry
# ===========================================================================


class TestSessionModel:
    """Verify that the Session model and service both honour session expiry."""

    def test_sh20_session_model_has_expires_at(self):
        """SH_20: Session ORM model must define an expires_at column."""
        from llm.seca.auth.models import Session  # noqa: PLC0415

        assert hasattr(Session, "expires_at"), (
            "Session model does not have an 'expires_at' attribute. "
            "Without a server-side expiry column, sessions cannot be revoked at the DB level."
        )

    def test_sh20b_session_expires_at_has_default(self):
        """SH_20b: Session.expires_at must have a non-null column default."""
        from llm.seca.auth.models import Session  # noqa: PLC0415

        col = Session.__table__.columns.get("expires_at")
        assert col is not None, "expires_at column not found in Session.__table__"
        assert col.default is not None, (
            "expires_at has no column default. "
            "New sessions must automatically receive an expiry timestamp."
        )

    def test_sh20c_service_checks_expires_at(self):
        """SH_20c: service.py get_player_by_session must reference session.expires_at."""
        source = _read("seca/auth/service.py")
        assert "expires_at" in source, (
            "service.py does not reference expires_at in get_player_by_session. "
            "Expired sessions must be rejected as defence-in-depth."
        )


# ===========================================================================
# SH_21 — CI Dependency Security
# ===========================================================================


class TestCIDependencySecurity:
    """Verify that CI-only dependencies satisfy known-CVE version floors."""

    def test_sh21_pytest_not_vulnerable_to_tmpdir_cve(self):
        """SH_21: pytest in requirements-ci.txt must be >= 8.1.0.

        pytest < 8.1.0 is vulnerable to GHSA-w234-x5rp-h73c (CVE-2024-3772):
        tmp_path directories were created world-readable (mode 0o777), allowing
        other local users to read or tamper with test fixture data during a run.
        Fixed in pytest 8.1.0 (tmp_path now applies mode 0o700).
        """
        reqs = (_REPO_ROOT / "llm" / "requirements-ci.txt").read_text(encoding="utf-8")
        pytest_version: str | None = None
        for line in reqs.splitlines():
            stripped = line.strip()
            # Match "pytest==X.Y.Z" but not "pytest-cov==..." or similar
            if stripped.startswith("pytest=="):
                pytest_version = stripped.split("==", 1)[1]
                break

        assert pytest_version is not None, (
            "pytest is not pinned with == in llm/requirements-ci.txt. "
            "Pin pytest to a specific version to ensure CVE audit coverage."
        )

        def _ver(v: str) -> tuple[int, ...]:
            return tuple(int(p) for p in v.split("."))

        assert _ver(pytest_version) >= (8, 1, 0), (
            f"pytest=={pytest_version} in requirements-ci.txt is vulnerable to "
            "GHSA-w234-x5rp-h73c (tmpdir world-readable permissions, CVE-2024-3772). "
            "Upgrade pytest to >= 8.1.0 to fix this CVE."
        )


# ===========================================================================
# SH_22 – SH_25 — v1 Hash Legacy Path (Bandit B324 SAST finding context)
# ===========================================================================


class TestV1HashLegacyPath:
    """Regression tests for the v1 hashing scheme (pbkdf2-sha256).

    SHA-256 is used as a normalization pre-step in _normalize_password_v1, not as the
    sole password protection.  PBKDF2 with 600 000 iterations is applied on top.
    These tests document the security properties that justify keeping the function
    unchanged and pin the auto-upgrade path so it cannot be accidentally removed.
    """

    def _make_v1_hash(self, password: str) -> str:
        import base64  # noqa: PLC0415
        import hashlib  # noqa: PLC0415

        from llm.seca.auth.hashing import _ITERATIONS, _SCHEME_V1, _normalize_password_v1  # noqa: PLC0415

        normalized = _normalize_password_v1(password)
        salt = os.urandom(16)
        dk = hashlib.pbkdf2_hmac("sha256", normalized, salt, _ITERATIONS)
        return f"${_SCHEME_V1}${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"

    def test_sh22_v1_hash_verifies_correctly(self):
        """SH_22: verify_password must accept the correct password for a v1-scheme hash.

        PBKDF2 (600 000 iterations) is the real work factor — the SHA-256 pre-step does not
        reduce cracking resistance meaningfully.  Changing _normalize_password_v1 would silently
        break authentication for all existing v1 users; this test pins the contract.
        """
        from llm.seca.auth.hashing import verify_password  # noqa: PLC0415

        pw = "legacy_user_password_v1_test!9"
        v1_hash = self._make_v1_hash(pw)
        assert verify_password(pw, v1_hash), (
            "verify_password rejected the correct password for a v1-scheme hash. "
            "Changing _normalize_password_v1 breaks authentication for existing v1 users."
        )
        assert not verify_password("wrong_password_xyz", v1_hash), (
            "verify_password accepted an incorrect password for a v1-scheme hash."
        )

    def test_sh23_v1_hash_triggers_needs_rehash(self):
        """SH_23: needs_rehash() must return True for any v1-scheme hash.

        Every successful v1 login must trigger an opportunistic upgrade to v2.  If
        needs_rehash() were to return False for v1 hashes, the migration path would stall
        and v1 hashes would persist in the database indefinitely.
        """
        from llm.seca.auth.hashing import needs_rehash  # noqa: PLC0415

        v1_hash = self._make_v1_hash("any_password_abc123!")
        assert needs_rehash(v1_hash), (
            "needs_rehash() returned False for a v1-scheme hash. "
            "All v1 hashes must be scheduled for upgrade to v2 on next login."
        )

    def test_sh24_hash_password_never_emits_v1_scheme(self):
        """SH_24: hash_password() must always produce a v2-scheme hash.

        No new v1 hashes should ever be written to the database.  If hash_password() were
        to emit a v1 hash, needs_rehash() would trigger on every login, causing an infinite
        rehash loop, and the SAST finding would apply to newly created accounts.
        """
        from llm.seca.auth.hashing import _SCHEME, _SCHEME_V1, hash_password  # noqa: PLC0415

        for pw in ("short1!A", "a" * 100, "unicode_pässwörð_123!"):
            h = hash_password(pw)
            scheme_field = h.split("$")[1] if h.startswith("$") else ""
            assert scheme_field == _SCHEME, (
                f"hash_password({pw!r}) emitted scheme {scheme_field!r} instead of {_SCHEME!r}: "
                f"{h[:40]!r}. hash_password() must always create v2-scheme hashes."
            )

    def test_sh25_service_login_contains_opportunistic_upgrade(self):
        """SH_25: service.py login() must contain the opportunistic v1→v2 upgrade.

        Without this, v1 hashes would accumulate in the database indefinitely and the SAST
        finding would remain permanently unmitigated.  This test pins the upgrade branch so
        it cannot be removed without failing CI.
        """
        source = _read("seca/auth/service.py")
        assert "needs_rehash" in source, (
            "service.py does not call needs_rehash(). "
            "The opportunistic v1→v2 upgrade path is missing from the login flow."
        )
        assert re.search(r"needs_rehash.*\n\s+.*hash_password|if needs_rehash", source), (
            "service.py does not contain the 'if needs_rehash → hash_password' upgrade branch. "
            "Every successful v1 login must rewrite the stored hash to v2."
        )
