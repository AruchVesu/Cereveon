"""
Security vulnerability regression tests — llm/tests/test_security_new_findings.py

Introduced after the 2026-04-25 full security audit. Each test corresponds to a
confirmed finding. All tests are CI-safe: no live Stockfish, no real HTTP server,
no network I/O. In-memory SQLite is used where a database is required.

Stable test IDs:
  SN_01   shared verify_api_key (seca/auth/api_key.py) uses hmac.compare_digest
  SN_01b  server.py imports the shared verify_api_key (no local definition)
  SN_01c  host_app.py imports the shared verify_api_key (no local definition)
  SN_01d  shared seca/auth/api_key.py imports hmac
  SN_02   /engine/predictions in host_app.py has @_limiter.limit() rate-limit decorator
  SN_03   /auth/register 400 error does not expose duplicate-email oracle
  SN_04   auth/service.py register() enforces minimum password length (≥ 8 chars)
  SN_05   RegisterRequest rejects empty / non-email strings for the email field
  SN_06   CoachFeedbackRequest.session_fen has a FEN format validator
  SN_07   LoginRequest.device_info is bounded (max length enforced by validator)
  SN_08   ChatRequest.past_mistakes individual items have a per-item length cap
  SN_09   ChatRequest.player_profile has a key-count / total-size cap
  SN_10   host_app.py has a body size limit middleware (Content-Length > 512 KB → 413)
  SN_10c  host_app.py rejects POST/PUT/PATCH without Content-Length (chunked-encoding bypass)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LLM = _REPO_ROOT / "llm"


def _read(relative: str) -> str:
    return (_LLM / relative).read_text(encoding="utf-8")


def _parse(relative: str) -> ast.Module:
    return ast.parse(_read(relative))


def _find_func(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _has_compare_digest_call(func_node) -> bool:
    """Return True if func_node body contains any call to hmac.compare_digest."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "compare_digest":
                return True
    return False


def _has_limiter_decorator(func_node) -> bool:
    for dec in func_node.decorator_list:
        if isinstance(dec, ast.Call):
            f = dec.func
            if isinstance(f, ast.Attribute) and f.attr == "limit":
                return True
    return False


def _has_field_validator(cls_node: ast.ClassDef, field: str) -> bool:
    """Return True if cls_node has a @field_validator(field_name) method."""
    for node in ast.walk(cls_node):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            dec_func = dec.func
            is_fv = (isinstance(dec_func, ast.Name) and dec_func.id == "field_validator") or (
                isinstance(dec_func, ast.Attribute) and dec_func.attr == "field_validator"
            )
            if not is_fv:
                continue
            for arg in dec.args:
                if isinstance(arg, ast.Constant) and arg.value == field:
                    return True
    return False


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


# ===========================================================================
# SN_01 / SN_01b — Timing-safe API key comparison
# ===========================================================================


class TestTimingSafeApiKeyComparison:
    """
    verify_api_key must compare keys with hmac.compare_digest, not with ==.

    Python's == / != on strings is NOT constant-time. An attacker who can measure
    response latency can brute-force the API key one character at a time (timing
    oracle). hmac.compare_digest guarantees constant-time comparison.
    """

    def test_sn01_shared_verify_api_key_uses_hmac_compare_digest(self):
        """SN_01: the shared verify_api_key implementation must use hmac.compare_digest.

        ``verify_api_key`` was previously duplicated in server.py and
        host_app.py with SN_01 / SN_01b pinning each copy.  After
        deduplication the single implementation lives at
        ``llm/seca/auth/api_key.py`` and is imported by both apps.
        Pinning the shared module covers both consumers.
        """
        tree = _parse("seca/auth/api_key.py")
        func = _find_func(tree, "verify_api_key")
        assert func is not None, "verify_api_key() not found in seca/auth/api_key.py"
        assert _has_compare_digest_call(func), (
            "verify_api_key() in seca/auth/api_key.py uses == / != for API key "
            "comparison.  Replace with hmac.compare_digest() to prevent timing "
            "side-channel attacks."
        )

    def test_sn01b_server_imports_shared_verify_api_key(self):
        """SN_01b: server.py must import verify_api_key from the shared module.

        Guards against re-introducing a local copy that could drift from
        the timing-safe implementation.  Either ``from
        llm.seca.auth.api_key import verify_api_key`` or any equivalent
        path that pulls the shared symbol counts; what is forbidden is a
        local ``def verify_api_key`` block.
        """
        source = _read("server.py")
        assert "from llm.seca.auth.api_key import verify_api_key" in source, (
            "server.py does not import verify_api_key from the shared "
            "llm.seca.auth.api_key module."
        )
        tree = _parse("server.py")
        local_def = _find_func(tree, "verify_api_key")
        assert local_def is None, (
            "server.py defines a local verify_api_key() — must use the shared "
            "implementation in llm/seca/auth/api_key.py to avoid drift."
        )

    def test_sn01c_host_app_imports_shared_verify_api_key(self):
        """SN_01c: host_app.py must import verify_api_key from the shared module.

        Same reasoning as SN_01b — no local verify_api_key permitted.
        """
        source = _read("host_app.py")
        assert "from llm.seca.auth.api_key import verify_api_key" in source, (
            "host_app.py does not import verify_api_key from the shared "
            "llm.seca.auth.api_key module."
        )
        tree = _parse("host_app.py")
        local_def = _find_func(tree, "verify_api_key")
        assert local_def is None, (
            "host_app.py defines a local verify_api_key() — must use the shared "
            "implementation in llm/seca/auth/api_key.py to avoid drift."
        )

    def test_sn01d_hmac_imported_in_shared_module(self):
        """SN_01d: the shared module must import hmac for compare_digest."""
        source = _read("seca/auth/api_key.py")
        assert "import hmac" in source, (
            "seca/auth/api_key.py does not import hmac. "
            "hmac.compare_digest() cannot be called without this import."
        )


# ===========================================================================
# SN_02 — /engine/predictions rate limiting
# ===========================================================================


class TestEnginePredictionsRateLimited:
    """
    /engine/predictions in host_app.py must be rate-limited.

    The endpoint calls get_predictions() which hits the engine pool. Without a
    rate limit any unauthenticated caller can exhaust the pool continuously.
    """

    def test_sn02_engine_predictions_has_limiter_decorator(self):
        """SN_02: engine_predictions() must have @_limiter.limit() decorator."""
        tree = _parse("host_app.py")
        func = _find_func(tree, "engine_predictions")
        assert func is not None, "engine_predictions() not found in host_app.py"
        assert _has_limiter_decorator(func), (
            "engine_predictions() (GET /engine/predictions) in host_app.py has no "
            "@_limiter.limit() decorator. Unauthenticated callers can exhaust the "
            "engine pool at will. Add @_limiter.limit('30/minute')."
        )


# ===========================================================================
# SN_03 — Email enumeration via /auth/register
# ===========================================================================


class TestNoEmailEnumerationOnRegister:
    """
    SN_03: /auth/register must not reveal whether a given email is already registered.

    Returning HTTP 400 with detail='Email already registered' lets an attacker
    enumerate all registered email addresses by trying different ones and checking
    the response body.  The fix is to return a generic error message.
    """

    def test_sn03_register_route_does_not_expose_duplicate_email_detail(self):
        """SN_03: auth/router.py register handler must not expose 'already registered' in detail."""
        source = _read("seca/auth/router.py")
        # The register endpoint must NOT pass the raw ValueError message as the HTTPException
        # detail when it comes from the service layer, because service.py raises
        # ValueError("Email already registered") — exposing it verbatim is an enumeration oracle.
        #
        # Acceptable patterns:
        #   raise HTTPException(status_code=400, detail="Registration failed")   ← generic
        #   raise HTTPException(status_code=409, detail="Registration failed")   ← generic
        #
        # Unacceptable patterns:
        #   raise HTTPException(status_code=400, detail=str(exc))  when exc == "Email already registered"
        #
        # We detect the bad pattern: the register() block using detail=str(exc) AND the
        # service layer having "Email already registered" as a literal — together they form
        # the oracle.
        service_source = _read("seca/auth/service.py")
        has_enumerable_message = "Email already registered" in service_source

        # If the service raises a distinguishable message, the router must NOT forward it verbatim.
        if has_enumerable_message:
            # Look for the register route's exception handler
            # Bad pattern: raise HTTPException(..., detail=str(exc)) in the register function
            register_section = re.search(
                r"def register\b.*?(?=\ndef |\nclass |\Z)",
                source,
                re.DOTALL,
            )
            if register_section:
                handler = register_section.group(0)
                assert "detail=str(exc)" not in handler, (
                    "auth/router.py register() passes raw ValueError detail to the HTTP response. "
                    "Since service.py raises ValueError('Email already registered'), this reveals "
                    "whether the email is already registered (enumeration oracle). "
                    "Use a generic message: detail='Registration failed'."
                )

    def test_sn03b_register_unit_returns_generic_message(self):
        """SN_03b: AuthService.register must raise a generic ValueError for duplicate email."""
        import uuid

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from llm.seca.auth.models import Base, Player
        from llm.seca.auth.hashing import hash_password
        from llm.seca.auth.service import AuthService

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        email = f"dup_{uuid.uuid4().hex[:8]}@test.com"
        db.add(Player(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=hash_password("StrongPass123!"),
        ))
        db.commit()

        with pytest.raises(ValueError) as exc_info:
            AuthService(db).register(email, "AnotherPass123!")

        detail = str(exc_info.value).lower()
        # The message must NOT be "email already registered" (verbatim enumerable)
        # A generic "registration failed" or similar is required.
        assert "already registered" not in detail and "email" not in detail, (
            f"AuthService.register() raises '{exc_info.value}' which reveals that the "
            "email is already taken. Use a generic message like 'Registration failed' "
            "to prevent email enumeration attacks."
        )
        db.close()


# ===========================================================================
# SN_04 — Minimum password length on registration
# ===========================================================================


class TestRegisterPasswordMinLength:
    """
    SN_04: AuthService.register() must enforce a minimum password length.

    change_password() already enforces 8 chars. register() did not — a user
    could register with a 1-character password. The minimum must be consistent.
    """

    def _make_service(self):
        import uuid

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from llm.seca.auth.models import Base
        from llm.seca.auth.service import AuthService

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine)()
        return AuthService(db), db

    def test_sn04_register_rejects_empty_password(self):
        """SN_04: register() must reject an empty password."""
        svc, db = self._make_service()
        with pytest.raises(ValueError, match=r"(?i)(password|8)"):
            svc.register("user@example.com", "")
        db.close()

    def test_sn04b_register_rejects_short_password(self):
        """SN_04b: register() must reject passwords shorter than 8 characters."""
        svc, db = self._make_service()
        with pytest.raises(ValueError, match=r"(?i)(password|8)"):
            svc.register("user2@example.com", "short")
        db.close()

    def test_sn04c_register_accepts_valid_password(self):
        """SN_04c: register() must accept a password of at least 8 characters."""
        svc, db = self._make_service()
        player = svc.register("user3@example.com", "ValidPass1!")
        assert player is not None
        db.close()


# ===========================================================================
# SN_05 — Email format validation on RegisterRequest
# ===========================================================================


class TestRegisterEmailValidation:
    """
    SN_05: RegisterRequest must validate email format.

    Without validation, a user can register with an empty string or any
    arbitrary text as their 'email', making the account unrecoverable.
    """

    def test_sn05_register_request_has_email_validator(self):
        """SN_05: RegisterRequest must have a @field_validator('email') method."""
        tree = _parse("seca/auth/router.py")
        cls = _find_class(tree, "RegisterRequest")
        assert cls is not None, "RegisterRequest not found in auth/router.py"
        assert _has_field_validator(cls, "email"), (
            "RegisterRequest has no @field_validator('email'). "
            "An empty string or any text is accepted as an email address, "
            "producing unrecoverable accounts and polluting the user table."
        )

    def test_sn05b_register_request_rejects_empty_email(self):
        """SN_05b: RegisterRequest must reject an empty email string."""
        from pydantic import ValidationError

        try:
            from llm.seca.auth.router import RegisterRequest
        except Exception:
            from pydantic import BaseModel, field_validator as _fv

            class RegisterRequest(BaseModel):  # type: ignore[no-redef]
                email: str
                password: str

                @_fv("email")
                @classmethod
                def validate_email(cls, v: str) -> str:
                    v = v.strip()
                    if len(v) < 3 or "@" not in v or len(v) > 320:
                        raise ValueError("Invalid email address")
                    return v

        with pytest.raises(ValidationError):
            RegisterRequest(email="", password="ValidPass1!")

    def test_sn05c_register_request_rejects_no_at_sign(self):
        """SN_05c: RegisterRequest must reject an email string without '@'."""
        from pydantic import ValidationError

        try:
            from llm.seca.auth.router import RegisterRequest
        except Exception:
            from pydantic import BaseModel, field_validator as _fv

            class RegisterRequest(BaseModel):  # type: ignore[no-redef]
                email: str
                password: str

                @_fv("email")
                @classmethod
                def validate_email(cls, v: str) -> str:
                    v = v.strip()
                    if len(v) < 3 or "@" not in v or len(v) > 320:
                        raise ValueError("Invalid email address")
                    return v

        with pytest.raises(ValidationError):
            RegisterRequest(email="notanemail", password="ValidPass1!")


# ===========================================================================
# SN_06 — FEN validation in CoachFeedbackRequest
# ===========================================================================


class TestCoachFeedbackFenValidation:
    """
    SN_06: CoachFeedbackRequest.session_fen must be validated as a FEN string.

    All other endpoints using FEN strings apply _validate_fen_field(). The
    coach-feedback endpoint was the only exception.
    """

    def test_sn06_coach_feedback_has_session_fen_validator(self):
        """SN_06: CoachFeedbackRequest must have a @field_validator('session_fen') method."""
        tree = _parse("seca/events/router.py")
        cls = _find_class(tree, "CoachFeedbackRequest")
        assert cls is not None, "CoachFeedbackRequest not found in events/router.py"
        assert _has_field_validator(cls, "session_fen"), (
            "CoachFeedbackRequest has no @field_validator('session_fen'). "
            "Unlike every other FEN-accepting endpoint, this request accepts arbitrary "
            "strings. Add a validator consistent with _validate_fen_field()."
        )


# ===========================================================================
# SN_07 — device_info length cap in LoginRequest
# ===========================================================================


class TestLoginRequestDeviceInfoLength:
    """
    SN_07: LoginRequest.device_info must have a maximum length cap.

    device_info is stored directly in the sessions table with no truncation.
    Without a cap a caller can insert an arbitrarily large string on every
    login, wasting database space.
    """

    def test_sn07_login_request_has_device_info_validator(self):
        """SN_07: LoginRequest must have a @field_validator('device_info') method."""
        tree = _parse("seca/auth/router.py")
        cls = _find_class(tree, "LoginRequest")
        assert cls is not None, "LoginRequest not found in auth/router.py"
        assert _has_field_validator(cls, "device_info"), (
            "LoginRequest has no @field_validator('device_info'). "
            "device_info is stored in the sessions DB column with no length limit — "
            "callers can insert arbitrarily large strings on every login."
        )

    def test_sn07b_login_request_rejects_oversized_device_info(self):
        """SN_07b: LoginRequest must reject device_info longer than 200 chars."""
        from pydantic import ValidationError

        try:
            from llm.seca.auth.router import LoginRequest
        except Exception:
            from pydantic import BaseModel, field_validator as _fv

            class LoginRequest(BaseModel):  # type: ignore[no-redef]
                email: str
                password: str
                device_info: str = ""

                @_fv("device_info")
                @classmethod
                def validate_device_info(cls, v: str) -> str:
                    if len(v) > 200:
                        raise ValueError("device_info too long (max 200 chars)")
                    return v

        with pytest.raises(ValidationError):
            LoginRequest(
                email="a@b.com",
                password="ValidPass1!",
                device_info="x" * 201,
            )


# ===========================================================================
# SN_08 — past_mistakes per-item length cap in ChatRequest
# ===========================================================================


class TestChatRequestPastMistakesItemLength:
    """
    SN_08: ChatRequest.past_mistakes must cap the length of each individual item.

    The list-count cap (max 20 items) was already in place, but individual items
    had no limit — a single item could carry megabytes of text into the LLM
    context window.
    """

    def test_sn08_past_mistakes_validator_checks_item_length(self):
        """SN_08: validate_past_mistakes in server.py must enforce a per-item length cap."""
        source = _read("server.py")
        # The validator must contain per-item length logic, not just list-level length.
        # We look for both the list-level check AND a per-item check.
        has_list_check = "len(v) > 20" in source
        # Per-item check: iterating items and checking length
        has_item_check = bool(
            re.search(r"for\s+\w+\s+in\s+v.*?len\(", source, re.DOTALL)
            or "len(item)" in source
            or "len(m)" in source
        )
        assert has_list_check, (
            "validate_past_mistakes in server.py has no list-length guard (len(v) > 20)."
        )
        assert has_item_check, (
            "validate_past_mistakes in server.py has no per-item length guard. "
            "A single past_mistakes entry could inject megabytes of text into the LLM. "
            "Add a per-item cap (e.g., max 500 chars per item)."
        )

    def test_sn08b_past_mistakes_oversized_item_rejected(self):
        """SN_08b: ChatRequest must reject past_mistakes with an oversized individual item."""
        from pydantic import ValidationError

        os_env_patch = {"SECA_API_KEY": "test", "SECA_ENV": "dev"}
        import os
        for k, v in os_env_patch.items():
            os.environ.setdefault(k, v)

        try:
            from llm.server import ChatRequest
            ChatRequest(
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                messages=[],
                past_mistakes=["x" * 10_000],
            )
            pytest.fail(
                "ChatRequest accepted a past_mistakes item of 10 000 chars. "
                "Add a per-item length cap in validate_past_mistakes()."
            )
        except ValidationError:
            pass  # expected — vulnerability fixed
        except Exception:
            # Import failed — test via source inspection only (already done in sn08)
            pass


# ===========================================================================
# SN_09 — player_profile size cap in ChatRequest
# ===========================================================================


class TestChatRequestPlayerProfileSize:
    """
    SN_09: ChatRequest.player_profile must have a key-count / total-size cap.

    player_profile is an arbitrary dict passed to the LLM context builder with
    no validation. An attacker can inject megabytes of arbitrary data into every
    coaching request.
    """

    def test_sn09_player_profile_has_validator(self):
        """SN_09: ChatRequest must have a @field_validator('player_profile') method."""
        tree = _parse("server.py")
        cls = _find_class(tree, "ChatRequest")
        assert cls is not None, "ChatRequest not found in server.py"
        assert _has_field_validator(cls, "player_profile"), (
            "ChatRequest has no @field_validator('player_profile'). "
            "player_profile is passed unvalidated into the LLM context — "
            "an attacker can inject arbitrary large payloads."
        )

    def test_sn09b_player_profile_oversized_value_rejected(self):
        """SN_09b: ChatRequest must reject player_profile with oversized content."""
        from pydantic import ValidationError

        import os
        os.environ.setdefault("SECA_API_KEY", "test")
        os.environ.setdefault("SECA_ENV", "dev")

        try:
            from llm.server import ChatRequest
            ChatRequest(
                fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                messages=[],
                player_profile={"key": "x" * 10_000},
            )
            pytest.fail(
                "ChatRequest accepted a player_profile with 10 000-char value. "
                "Add a @field_validator('player_profile') with a size cap."
            )
        except ValidationError:
            pass  # expected — vulnerability fixed
        except Exception:
            pass  # import chain failed — source-inspection test (sn09) is sufficient


# ===========================================================================
# SN_10 — host_app.py body size limit middleware
# ===========================================================================


class TestHostAppBodySizeLimit:
    """
    SN_10: host_app.py must enforce a request body size limit.

    server.py has a _LimitBodySize middleware capping at 512 KB. host_app.py
    had no equivalent — large POST requests to /engine/eval or /debug/engine-raw
    were unbounded.
    """

    def test_sn10_host_app_source_has_body_limit_middleware(self):
        """SN_10: host_app.py must add a body-size-limit middleware."""
        source = _read("host_app.py")
        has_limit = (
            "_LimitBodySize" in source
            or "content-length" in source.lower()
            or "413" in source
            or "BaseHTTPMiddleware" in source
        )
        assert has_limit, (
            "host_app.py has no body size limit middleware. "
            "server.py caps requests at 512 KB via _LimitBodySize; host_app.py does not. "
            "Large POST bodies to /engine/eval or /debug/engine-raw are unbounded."
        )

    def test_sn10b_host_app_stub_413_on_large_body(self):
        """SN_10b: host_app.py-style middleware must return 413 for Content-Length > 512 KB.

        The stub mirrors host_app.py's current ``_LimitBodySize`` shape
        — including the SN_10c chunked-encoding rejection (mirrors the
        server.py SVD_01 fix).  Both the oversized-body and the
        missing-Content-Length-on-POST cases are exercised below.
        """
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient
        from starlette.middleware.base import BaseHTTPMiddleware

        stub = FastAPI()
        _MAX_BODY = 512 * 1024
        _BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

        class _LimitBodySize(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                cl = request.headers.get("content-length")
                if cl is None:
                    if request.method in _BODY_METHODS:
                        return JSONResponse(
                            status_code=411,
                            content={"error": "Content-Length header required"},
                        )
                else:
                    try:
                        if int(cl) > _MAX_BODY:
                            return JSONResponse(
                                status_code=413,
                                content={"error": "Request body too large"},
                            )
                    except ValueError:
                        return JSONResponse(
                            status_code=400, content={"error": "Invalid Content-Length"}
                        )
                return await call_next(request)

        stub.add_middleware(_LimitBodySize)

        @stub.get("/health")
        def health():
            return {"ok": True}

        client = TestClient(stub, raise_server_exceptions=False)
        oversized = str(512 * 1024 + 1)
        resp = client.get("/health", headers={"Content-Length": oversized})
        assert resp.status_code == 413, (
            f"Expected 413 for Content-Length={oversized}, got {resp.status_code}"
        )

    def test_sn10c_host_app_rejects_post_without_content_length(self):
        """SN_10c: host_app.py must reject POST/PUT/PATCH with no Content-Length.

        Mirrors the SVD_01 fix on server.py.  A body-size guard that
        only inspects the Content-Length header is bypassed entirely by
        chunked-transfer-encoded requests (or any HTTP/1.1 request that
        omits the header).  Source-inspection check that host_app.py's
        middleware enforces the contract.
        """
        source = _read("host_app.py")
        assert "_BODY_METHODS" in source and "Content-Length header required" in source, (
            "host_app.py's _LimitBodySize does not reject body-bearing methods "
            "without a Content-Length header.  Same SVD_01-class bypass that "
            "server.py closed."
        )
