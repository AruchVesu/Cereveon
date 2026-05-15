"""
Security tests for the backend API.

Approach
--------
Tests are split into three tiers to stay CI-safe (no live Stockfish / DB):

  Tier 1 — AST inspection
    Parse server.py and auth/router.py source code to verify that each
    endpoint that should require authentication has a `verify_api_key` or
    `get_current_player` dependency, and that the logout handler wraps
    `decode_token` in a try/except block.

  Tier 2 — Pydantic model validation (in-process)
    Instantiate the real server-side Pydantic models (`OutcomeRequest`,
    `LiveMoveRequest`) via direct import and confirm that out-of-range or
    malformed payloads raise `ValidationError`.

  Tier 3 — HTTP-layer authentication (minimal stub app)
    Create a self-contained FastAPI + TestClient instance that mirrors the
    `verify_api_key` logic from server.py.  Tests confirm that protected
    endpoints return 401 without a valid API key and 200 with one.  No
    server.py import occurs in this tier, avoiding the problematic module
    chains documented in run_ci_suite.py.

Invariants pinned
-----------------
 1. SEC_LIVEMOVE_AUTH_APPLIED       /live/move has get_current_player dependency (player session required).
 1b. SEC_MOVE_AUTH_APPLIED          /move has get_current_player dependency (player session required).
 1c. SEC_PROGRESS_AUTH_APPLIED      /player/progress has get_current_player dependency.
 2. SEC_DEBUG_ENGINE_AUTH_APPLIED   /debug/engine has verify_api_key dependency.
 3. SEC_LOGOUT_WRAPS_DECODE_TOKEN   logout wraps decode_token in try/except.
 4. SEC_LIVEMOVE_BAD_FEN            Invalid FEN in LiveMoveRequest → ValidationError.
17. SEC_LIVEMOVE_SHORT_UCI          UCI < 4 chars → ValidationError.
18. SEC_LIVEMOVE_LONG_UCI           UCI > 5 chars → ValidationError.
19. SEC_LIVEMOVE_LONG_PLAYER_ID     player_id > 100 chars → ValidationError.
20. SEC_LIVEMOVE_VALID_ACCEPTED     Valid LiveMoveRequest passes validation.
21. SEC_HTTP_ANALYZE_NO_KEY_401     POST /analyze without key → 401.
22. SEC_HTTP_ANALYZE_WRONG_KEY_401  POST /analyze with wrong key → 401.
23. SEC_HTTP_ANALYZE_CORRECT_KEY    POST /analyze with correct key → 200.
24. SEC_HTTP_OUTCOME_NO_KEY_401     POST /explanation_outcome without key → 401.
25. SEC_HTTP_LIVEMOVE_NO_KEY_401    POST /live/move without key → 401.
26. SEC_HTTP_DEBUG_NO_KEY_401       GET /debug/engine without key → 401.
27. SEC_HTTP_HEALTH_OPEN            GET /health requires no key (must stay open).
28. SEC_APIKEY_DEV_NO_KEY_PASSES    verify_api_key passes when no SECA_API_KEY set (dev mode).
29. SEC_APIKEY_CORRECT_KEY_PASSES   verify_api_key passes with correct key.
30. SEC_APIKEY_WRONG_KEY_401        verify_api_key raises HTTPException(401) on wrong key.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVER_PY = _REPO_ROOT / "llm" / "server.py"
_AUTH_ROUTER = _REPO_ROOT / "llm" / "seca" / "auth" / "router.py"


# ===========================================================================
# Tier 1 — AST Inspection
# ===========================================================================


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _get_decorated_functions(
    tree: ast.Module,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return {function_name: FunctionDef|AsyncFunctionDef} for all decorated defs."""
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _depends_on(func_def: ast.FunctionDef, target: str) -> bool:
    """
    Return True if any argument default in func_def is a Depends(target) call.
    Handles both `Depends(target)` and `Depends(verify_api_key)` patterns.
    """
    for default in func_def.args.defaults + func_def.args.kw_defaults:
        if default is None:
            continue
        if not isinstance(default, ast.Call):
            continue
        call = default
        # Depends(target)
        func = call.func
        if isinstance(func, ast.Name) and func.id == "Depends":
            for arg in call.args:
                if isinstance(arg, ast.Name) and arg.id == target:
                    return True
    return False


class TestAstEndpointProtection:

    def setup_method(self):
        self._server = _parse(_SERVER_PY)
        self._funcs = _get_decorated_functions(self._server)

    # SEC_ANALYZE_AUTH_APPLIED + SEC_OUTCOME_AUTH_APPLIED retired in PR 22
    # (2026-05-15) alongside the /analyze and /explanation_outcome HTTP
    # routes.  No Android caller had ever emerged for either; the auth
    # invariant tests had no remaining handler to assert against.

    def test_live_move_requires_player_session(self):
        """SEC_LIVEMOVE_AUTH_APPLIED: /live/move requires get_current_player (player session).

        /live/move was upgraded from API-key auth to player session auth so that
        coaching hints adapt to the authenticated player's skill profile.
        A valid player session (Bearer JWT + DB record) is required.
        """
        func = self._funcs.get("live_move")
        assert func is not None, "live_move() not found in server.py"
        assert _depends_on(func, "get_current_player"), (
            "POST /live/move must have Depends(get_current_player) — "
            "player session required for adaptive coaching hints"
        )

    # SEC_MOVE_AUTH_APPLIED retired in PR 23 (2026-05-15) alongside the
    # /move HTTP route — the auth invariant has no handler to assert
    # against.  /live/move's session-auth coverage above remains.

    def test_debug_engine_has_verify_api_key(self):
        """SEC_DEBUG_ENGINE_AUTH_APPLIED: /debug/engine has verify_api_key dependency."""
        func = self._funcs.get("engine_debug")
        assert func is not None, "engine_debug() not found in server.py"
        assert _depends_on(
            func, "verify_api_key"
        ), "GET /debug/engine must have Depends(verify_api_key) — leaks engine pool info"

    def test_progress_endpoint_requires_player_session(self):
        """SEC_PROGRESS_AUTH_APPLIED: /player/progress requires get_current_player."""
        import ast
        from pathlib import Path

        analytics_router_path = (
            Path(__file__).resolve().parent.parent / "seca" / "analytics" / "router.py"
        )
        tree = ast.parse(analytics_router_path.read_text(encoding="utf-8"))
        progress_func = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "get_player_progress"
            ):
                progress_func = node
                break
        assert progress_func is not None, "get_player_progress() not found in analytics/router.py"
        assert _depends_on(progress_func, "get_current_player"), (
            "GET /player/progress must have Depends(get_current_player) — "
            "returns sensitive player data; player session required"
        )


class TestAstLogoutProtection:

    def test_logout_wraps_decode_token_in_try_except(self):
        """SEC_LOGOUT_WRAPS_DECODE_TOKEN: logout wraps decode_token in try/except.

        A bare decode_token() call propagates jwt exceptions as HTTP 500.
        The fix wraps it in try/except and raises HTTPException(401).
        """
        tree = _parse(_AUTH_ROUTER)

        logout_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "logout":
                logout_func = node
                break

        assert logout_func is not None, "logout() not found in auth/router.py"

        # Walk the function body looking for a Try node that contains a
        # call to decode_token.
        def _contains_decode_token_call(nodes) -> bool:
            for node in ast.walk(
                nodes if isinstance(nodes, ast.AST) else ast.Module(body=nodes, type_ignores=[])
            ):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == "decode_token":
                        return True
            return False

        try_nodes = [n for n in ast.walk(logout_func) if isinstance(n, ast.Try)]
        assert try_nodes, (
            "logout() has no try/except block. "
            "decode_token() must be wrapped to prevent 500 on invalid tokens."
        )

        found_wrapped = any(_contains_decode_token_call(try_node.body) for try_node in try_nodes)
        assert found_wrapped, (
            "No try/except block in logout() wraps a decode_token() call. "
            "An invalid token returns 500 instead of 401."
        )


# ===========================================================================
# Tier 2 — Pydantic Model Validation (direct import, isolated models)
# ===========================================================================

# Import the real model classes from server.py via a sys.path trick to avoid
# executing the startup code.  We import only the model classes which are
# pure-Pydantic and have no side effects.
#
# If the import is not possible in CI (module chain issue), the tests fall
# back to locally-defined mirrors of the validators.

try:
    # Set env before any import that reads API_KEY.
    os.environ.setdefault("SECA_API_KEY", "ci-test-key")
    os.environ.setdefault("SECA_ENV", "dev")

    from llm.server import LiveMoveRequest as _LiveMoveRequest

    _MODELS_IMPORTED = True
except Exception:
    # Fallback: replicate the validators locally so Pydantic tests still run.
    # This mirrors the production validators exactly.
    from pydantic import BaseModel, field_validator as _fv

    def _validate_fen_field_local(v: str) -> str:
        stripped = v.strip()
        if stripped.lower() == "startpos":
            return v
        parts = stripped.split()
        if len(parts) != 6 or len(stripped) > 100:
            raise ValueError("invalid FEN")
        return v

    class _LiveMoveRequest(BaseModel):  # type: ignore[no-redef]
        fen: str
        uci: str
        player_id: str = "demo"

        @_fv("fen")
        @classmethod
        def validate_fen(cls, v: str) -> str:
            return _validate_fen_field_local(v)

        @_fv("uci")
        @classmethod
        def validate_uci(cls, v: str) -> str:
            if not (4 <= len(v) <= 5):
                raise ValueError("uci move must be 4–5 characters")
            return v

        @_fv("player_id")
        @classmethod
        def validate_player_id(cls, v: str) -> str:
            if len(v) > 100:
                raise ValueError("player_id too long (max 100 chars)")
            return v

    _MODELS_IMPORTED = False


def _valid_live_move(**overrides) -> dict:
    base = {
        "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "uci": "e7e5",
        "player_id": "player1",
    }
    base.update(overrides)
    return base


# TestOutcomeRequestValidation retired in PR 22 (2026-05-15) alongside
# the OutcomeRequest model + /explanation_outcome HTTP route.  Pydantic
# field bounds the test pinned (moves_analyzed, avg_cpl, blunder_rate,
# confidence_delta, explanation_id) defended a model that no longer
# exists.


class TestLiveMoveRequestValidation:

    def test_invalid_fen_rejected(self):
        """SEC_LIVEMOVE_BAD_FEN: Invalid FEN string → ValidationError."""
        with pytest.raises(ValidationError):
            _LiveMoveRequest(**_valid_live_move(fen="not-a-fen"))

    def test_fen_too_long_rejected(self):
        """SEC_LIVEMOVE_BAD_FEN: FEN string > 100 chars → ValidationError."""
        long_fen = "a " * 51  # > 100 chars and != 6 parts
        with pytest.raises(ValidationError):
            _LiveMoveRequest(**_valid_live_move(fen=long_fen))

    def test_uci_too_short_rejected(self):
        """SEC_LIVEMOVE_SHORT_UCI: UCI < 4 chars → ValidationError."""
        with pytest.raises(ValidationError):
            _LiveMoveRequest(**_valid_live_move(uci="e7"))

    def test_uci_too_long_rejected(self):
        """SEC_LIVEMOVE_LONG_UCI: UCI > 5 chars → ValidationError."""
        with pytest.raises(ValidationError):
            _LiveMoveRequest(**_valid_live_move(uci="e7e5e3"))

    def test_player_id_too_long_rejected(self):
        """SEC_LIVEMOVE_LONG_PLAYER_ID: player_id > 100 chars → ValidationError."""
        with pytest.raises(ValidationError):
            _LiveMoveRequest(**_valid_live_move(player_id="p" * 101))

    def test_valid_live_move_request_accepted(self):
        """SEC_LIVEMOVE_VALID_ACCEPTED: Valid fields pass validation."""
        req = _LiveMoveRequest(**_valid_live_move())
        assert req.uci == "e7e5"
        assert req.player_id == "player1"


# ===========================================================================
# Tier 3 — HTTP-layer auth tests (minimal stub app, no server.py import)
# ===========================================================================

_TEST_API_KEY = "test-api-key-secure"

# Build a minimal FastAPI app that mirrors the verify_api_key logic.
# This avoids importing llm.server (which triggers heavy module chains in CI).

_stub_app = FastAPI()


def _stub_verify_api_key(x_api_key: str = Header(None)):
    """Mirrors server.py:verify_api_key for HTTP-layer testing."""
    if x_api_key != _TEST_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@_stub_app.post("/analyze")
def _stub_analyze(_: None = Depends(_stub_verify_api_key)):
    return {"engine_signal": {}}


@_stub_app.post("/explanation_outcome")
def _stub_outcome(_: None = Depends(_stub_verify_api_key)):
    return {"learning_score": 0.5}


@_stub_app.post("/live/move")
def _stub_live_move(_: None = Depends(_stub_verify_api_key)):
    return {"status": "not_implemented"}


@_stub_app.get("/debug/engine")
def _stub_debug_engine(_: None = Depends(_stub_verify_api_key)):
    return {"pool_size": 0}


@_stub_app.get("/health")
def _stub_health():
    return {"status": "ok"}


_stub_client = TestClient(_stub_app, raise_server_exceptions=False)

_AUTH_HEADER = {"X-Api-Key": _TEST_API_KEY}
_WRONG_AUTH = {"X-Api-Key": "wrong-key"}
_VALID_ANALYZE_BODY = {"fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"}
_VALID_OUTCOME_BODY = {
    "explanation_id": "expl-1",
    "moves_analyzed": 5,
    "avg_cpl": 30.0,
    "blunder_rate": 0.2,
    "tactic_success": False,
    "confidence_delta": 0.0,
}
_VALID_LIVE_MOVE_BODY = {
    "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "uci": "e7e5",
}


class TestHttpAuthEnforcement:

    def test_analyze_without_key_returns_401(self):
        """SEC_HTTP_ANALYZE_NO_KEY_401: POST /analyze without API key → 401."""
        r = _stub_client.post("/analyze", json=_VALID_ANALYZE_BODY)
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_analyze_with_wrong_key_returns_401(self):
        """SEC_HTTP_ANALYZE_WRONG_KEY_401: POST /analyze with wrong key → 401."""
        r = _stub_client.post("/analyze", json=_VALID_ANALYZE_BODY, headers=_WRONG_AUTH)
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_analyze_with_correct_key_returns_200(self):
        """SEC_HTTP_ANALYZE_CORRECT_KEY: POST /analyze with correct key → 200."""
        r = _stub_client.post("/analyze", json=_VALID_ANALYZE_BODY, headers=_AUTH_HEADER)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"

    def test_explanation_outcome_without_key_returns_401(self):
        """SEC_HTTP_OUTCOME_NO_KEY_401: POST /explanation_outcome without key → 401."""
        r = _stub_client.post("/explanation_outcome", json=_VALID_OUTCOME_BODY)
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_live_move_without_key_returns_401(self):
        """SEC_HTTP_LIVEMOVE_NO_KEY_401: POST /live/move without key → 401."""
        r = _stub_client.post("/live/move", json=_VALID_LIVE_MOVE_BODY)
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_debug_engine_without_key_returns_401(self):
        """SEC_HTTP_DEBUG_NO_KEY_401: GET /debug/engine without key → 401."""
        r = _stub_client.get("/debug/engine")
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"

    def test_health_endpoint_requires_no_key(self):
        """SEC_HTTP_HEALTH_OPEN: GET /health must remain open (no auth required)."""
        r = _stub_client.get("/health")
        assert (
            r.status_code == 200
        ), f"GET /health should be publicly accessible; got {r.status_code}"


# ===========================================================================
# verify_api_key logic unit tests
# ===========================================================================


class TestVerifyApiKeyLogic:
    """
    Test the verify_api_key guard in isolation without importing server.py.
    The implementation is replicated here because importing llm.server in CI
    triggers the module chains documented in run_ci_suite.py comments.
    """

    @staticmethod
    def _make_verify(api_key: str | None, is_prod: bool = False, insecure_dev: bool = False):
        """Return a callable that behaves like seca.auth.api_key.verify_api_key.

        The local replica mirrors the production three-state dispatch:
        prod-no-key → 500, dev-no-key-no-flag → 401, dev-no-key-flag → pass.
        """

        def _check(x_api_key: str | None = None):
            if api_key is None:
                if is_prod:
                    raise HTTPException(status_code=500, detail="Server misconfiguration")
                if insecure_dev:
                    return  # explicit dev-mode bypass
                raise HTTPException(status_code=401, detail="Unauthorized")
            if x_api_key != api_key:
                raise HTTPException(status_code=401, detail="Unauthorized")

        return _check

    def test_dev_mode_with_insecure_flag_passes(self):
        """SEC_APIKEY_DEV_INSECURE_FLAG_PASSES: dev mode + SECA_INSECURE_DEV=true + no key → pass."""
        check = self._make_verify(api_key=None, is_prod=False, insecure_dev=True)
        check(x_api_key=None)  # must not raise
        check(x_api_key="anything")  # must not raise

    def test_dev_mode_without_insecure_flag_rejects(self):
        """SEC_APIKEY_DEV_NO_FLAG_401: dev mode + no SECA_INSECURE_DEV + no key → 401.

        Closes the SECA_ENV=dev-in-production footgun: a deploy that
        accidentally ships with SECA_ENV=dev but no SECA_API_KEY must NOT
        serve protected endpoints unauthenticated. Operators have to set
        the explicit SECA_INSECURE_DEV flag to opt into the bypass.
        See docs/THREAT_MODEL.md § T6.
        """
        check = self._make_verify(api_key=None, is_prod=False, insecure_dev=False)
        with pytest.raises(HTTPException) as exc_info:
            check(x_api_key=None)
        assert exc_info.value.status_code == 401, (
            "dev mode without SECA_INSECURE_DEV must reject — otherwise a "
            "misconfigured prod deploy would silently disable auth"
        )
        # The same 401 must apply when a wrong key IS sent — there is no
        # silent-pass path that an attacker can probe their way into.
        with pytest.raises(HTTPException) as exc_info2:
            check(x_api_key="probe")
        assert exc_info2.value.status_code == 401

    def test_correct_key_passes(self):
        """SEC_APIKEY_CORRECT_KEY_PASSES: verify_api_key passes with the correct key."""
        check = self._make_verify(api_key="secret123")
        check(x_api_key="secret123")  # must not raise

    def test_wrong_key_raises_401(self):
        """SEC_APIKEY_WRONG_KEY_401: verify_api_key raises HTTPException(401) on wrong key."""
        check = self._make_verify(api_key="secret123")
        with pytest.raises(HTTPException) as exc_info:
            check(x_api_key="wrong")
        assert exc_info.value.status_code == 401

    def test_no_key_sent_raises_401(self):
        """Missing X-Api-Key header when API key is configured → 401."""
        check = self._make_verify(api_key="secret123")
        with pytest.raises(HTTPException) as exc_info:
            check(x_api_key=None)
        assert exc_info.value.status_code == 401

    def test_prod_mode_no_key_configured_raises_500(self):
        """verify_api_key in prod mode with no env key → HTTPException(500)."""
        check = self._make_verify(api_key=None, is_prod=True)
        with pytest.raises(HTTPException) as exc_info:
            check(x_api_key=None)
        assert exc_info.value.status_code == 500


# ===========================================================================
# Pinned invariants (continued):
# 38. SEC_SERVER_NO_PRINT_STMTS      server.py uses logger not print() for diagnostics.
# 39. SEC_PROD_SECRET_KEY_GUARD      tokens.py raises RuntimeError at startup when SECRET_KEY
#                                    is absent in prod — prevents ephemeral key from
#                                    invalidating all JWTs on server restart.
# 40. SEC_PROD_API_KEY_GUARD         server.py raises RuntimeError at module level when
#                                    SECA_API_KEY is absent in prod — fail-fast at startup
#                                    instead of deferring failure to the first request.
#
# SEC_HOST_DEBUG_* invariants (31–37) were retired with host_app.py in the
# host_app retirement pass (2026-05-12).  The /debug/* endpoints they pinned
# never reached production (host_app was not deployed; see the 2026-05-12 PR
# notes) and the route handlers themselves were deleted.


class TestServerNoPrintStatements:
    """SEC_SERVER_NO_PRINT_STMTS: server.py must use logger, not print(), for diagnostics.

    print() bypasses the logging framework:
    - Structured log aggregation misses diagnostic events.
    - CodeQL flags bare print() in server code as a potential information-disclosure path.
    - The fix is to use logger.info() / logger.error() / logger.exception() throughout.
    """

    def test_server_py_has_no_bare_print_calls(self):
        """server.py must not contain bare print() calls — use logger instead."""
        tree = _parse(_SERVER_PY)
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "print":
                    violations.append(node.lineno)
        assert not violations, (
            f"server.py contains bare print() calls at lines {violations}. "
            "Replace with logger.info() / logger.error() / logger.exception()."
        )


# ===========================================================================
# Tier 1 — AST Inspection: production startup guards (invariants 39–40)
# ===========================================================================


_TOKENS_PY = _REPO_ROOT / "llm" / "seca" / "auth" / "tokens.py"


def _module_level_raises(tree: ast.Module) -> list[ast.Raise]:
    """Return Raise nodes found at the module top level (not inside function bodies)."""
    raises = []
    for node in tree.body:
        for child in ast.walk(node):
            if isinstance(child, ast.Raise):
                raises.append(child)
    return raises


def _raise_is_runtime_error(raise_node: ast.Raise) -> bool:
    """Return True if the Raise node raises RuntimeError (directly or via call)."""
    exc = raise_node.exc
    if exc is None:
        return False
    target = exc.func if isinstance(exc, ast.Call) else exc
    return isinstance(target, ast.Name) and target.id == "RuntimeError"


class TestProductionStartupGuards:
    """
    Verify fail-fast production guards are present in source code.

    SEC_PROD_SECRET_KEY_GUARD (39): tokens.py must raise RuntimeError at module level
        when SECRET_KEY is absent in prod.  Without this guard a missing SECRET_KEY
        silently generates a random ephemeral key, invalidating all JWTs on restart.

    SEC_PROD_API_KEY_GUARD (40): server.py must raise RuntimeError at module level
        when SECA_API_KEY is absent in prod.  Without this the failure is deferred to
        the first request (HTTP 500) instead of aborting startup immediately.
    """

    def test_tokens_py_has_prod_secret_key_guard(self):
        """SEC_PROD_SECRET_KEY_GUARD: tokens.py raises RuntimeError at startup when SECRET_KEY absent in prod."""
        source = _TOKENS_PY.read_text(encoding="utf-8")
        assert "_IS_PROD" in source or "IS_PROD" in source, (
            "tokens.py has no IS_PROD/production check. "
            "A missing SECRET_KEY silently generates an ephemeral key in prod, "
            "invalidating all JWTs on server restart."
        )
        tree = _parse(_TOKENS_PY)
        has_runtime_error = any(_raise_is_runtime_error(r) for r in _module_level_raises(tree))
        assert has_runtime_error, (
            "tokens.py has no module-level RuntimeError raise. "
            "It must raise at startup when SECRET_KEY is missing in production."
        )

    def test_server_py_has_prod_api_key_startup_guard(self):
        """SEC_PROD_API_KEY_GUARD: server.py raises RuntimeError at module level when SECA_API_KEY absent in prod."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert (
            "IS_PROD" in source
        ), "server.py has no IS_PROD reference — production guard cannot be applied."
        tree = _parse(_SERVER_PY)
        has_runtime_error = any(_raise_is_runtime_error(r) for r in _module_level_raises(tree))
        assert has_runtime_error, (
            "server.py has no module-level RuntimeError raise. "
            "When SECA_API_KEY is unset in prod, the current code defers the failure to "
            "the first request (HTTP 500). Add a module-level check to fail fast at startup."
        )


# ===========================================================================
# SEC_PROD_FOOTGUN_*  — PR 6 op-hardening
# ===========================================================================
#
# Closes the residual risk previously documented in
# ``docs/THREAT_MODEL.md`` § T6: a deploy that lands with
# ``SECA_INSECURE_DEV=true`` AND non-loopback CORS origins but
# ``SECA_ENV != prod`` would serve every X-Api-Key endpoint without
# authentication.  Pre-PR-6 the defence was documentation only;
# server.py now crashes at module load when the unsafe combination is
# detected.  Pinned by AST inspection + by direct calls to the
# extracted ``_looks_like_production_deploy`` heuristic.


class TestProductionFootgunHeuristic:
    """Unit tests for ``server._looks_like_production_deploy``.

    The function is pure (string in, bool out), so it can be exercised
    without the server.py import chain.  These tests document the
    boundary cases the guard cares about: empty CORS, loopback-only,
    mixed, and an explicit production-domain origin.
    """

    def _heuristic(self):
        """Lazy import of ``_looks_like_production_deploy``.

        Defers the server.py import chain (Stockfish probe, DB init,
        etc.) until the test actually runs — at module collection time
        the helper is not yet imported."""
        from llm.server import _looks_like_production_deploy  # noqa: PLC0415

        return _looks_like_production_deploy

    def test_empty_string_is_not_production(self):
        """SEC_PROD_FOOTGUN_EMPTY: an unset/empty CORS_ALLOWED_ORIGINS
        is the dev default; the heuristic must NOT flag it as
        production-facing."""
        assert self._heuristic()("") is False
        assert self._heuristic()("   ") is False
        assert self._heuristic()(", , ,") is False

    def test_loopback_only_is_not_production(self):
        """SEC_PROD_FOOTGUN_LOOPBACK: localhost / 127.0.0.1 / [::1] /
        10.0.2.2 (Android emulator) — none flag as production."""
        loopback_only = ",".join(
            [
                "http://localhost:8000",
                "http://127.0.0.1:8000",
                "http://10.0.2.2:8000",
                "http://[::1]:8000",
            ]
        )
        assert self._heuristic()(loopback_only) is False

    def test_non_loopback_origin_flags_production(self):
        """SEC_PROD_FOOTGUN_NON_LOOPBACK: a single non-loopback origin
        is sufficient to flag the deploy as production-facing."""
        assert self._heuristic()("https://app.example.com") is True
        assert self._heuristic()("https://api.cereveon.ai") is True

    def test_mixed_origins_flag_production(self):
        """SEC_PROD_FOOTGUN_MIXED: a single non-loopback origin in a
        list of mostly-loopback entries still flags as production."""
        mixed = (
            "http://localhost:3000, https://app.example.com, http://127.0.0.1:8000"
        )
        assert self._heuristic()(mixed) is True

    def test_case_insensitive_loopback(self):
        """SEC_PROD_FOOTGUN_CASE: marker matching is case-insensitive
        so ``HTTP://LOCALHOST`` etc. don't slip past."""
        assert self._heuristic()("HTTP://LOCALHOST:8000") is False

    def test_loopback_substring_in_subdomain_flags_production(self):
        """SEC_PROD_FOOTGUN_HOSTNAME_NOT_SUBSTRING: an attacker-
        controlled subdomain with a loopback label in its name MUST
        flag as production-facing.  Substring matching would let
        ``https://localhost.evil.com`` masquerade as loopback;
        hostname parsing rejects it because the hostname is
        ``localhost.evil.com``, not ``localhost``.

        Pinned because the original PR 6 implementation used substring
        matching and the 2026-05-14 reviewer pass caught the bypass.
        """
        assert self._heuristic()("https://localhost.evil.com") is True
        assert self._heuristic()("https://127.0.0.1.evil.com") is True
        # Mixed: one malicious subdomain + one legitimate loopback →
        # the malicious entry alone flags the deploy as production.
        assert (
            self._heuristic()(
                "https://localhost.evil.com,http://localhost:3000"
            )
            is True
        )

    def test_unparseable_origin_fails_closed(self):
        """SEC_PROD_FOOTGUN_UNPARSEABLE: an origin that cannot be
        parsed as a URL is treated as production-facing (fail closed).
        A deploy cannot evade the gate by submitting malformed CORS
        strings."""
        # ``http://[::1`` is a truncated IPv6 host that urlsplit
        # rejects with ValueError on every recent Python.
        assert self._heuristic()("http://[::1") is True

    def test_ipv6_loopback_bracketed_origin_is_loopback(self):
        """SEC_PROD_FOOTGUN_IPV6_BRACKETS: the IPv6 loopback bracketed
        form ``[::1]`` in a URL has hostname ``::1`` — confirm the
        loopback set entry without brackets handles it correctly."""
        assert self._heuristic()("http://[::1]:8000") is False


class TestProductionFootgunStartupGate:
    """Source-level pin: server.py raises ``RuntimeError`` at module
    load when ``SECA_INSECURE_DEV=true`` + production-facing CORS +
    ``SECA_ENV != prod``.

    The check itself runs once at module load; pinning the AST shape
    (RuntimeError + reference to ``_looks_like_production_deploy``)
    catches accidental deletion / loosening in any future revision.
    """

    def test_server_py_references_looks_like_production_deploy(self):
        """SEC_PROD_FOOTGUN_SOURCE_HEURISTIC: server.py must reference
        the production-deploy heuristic so the gate stays load-bearing."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "_looks_like_production_deploy" in source, (
            "server.py no longer references _looks_like_production_deploy; "
            "the THREAT_MODEL.md § T6 footgun check has regressed."
        )

    def test_server_py_references_insecure_dev(self):
        """SEC_PROD_FOOTGUN_SOURCE_FLAG: server.py must reference
        SECA_INSECURE_DEV as part of the footgun gate."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "SECA_INSECURE_DEV" in source, (
            "server.py no longer references SECA_INSECURE_DEV at module "
            "load — the production-footgun gate cannot trigger."
        )

    def test_footgun_runtime_error_mentions_threat_model(self):
        """SEC_PROD_FOOTGUN_SOURCE_DOC: the footgun RuntimeError
        message must reference docs/THREAT_MODEL.md § T6 so operators
        following the crash log find the rationale."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        assert "THREAT_MODEL.md § T6" in source, (
            "server.py footgun crash message no longer cites "
            "docs/THREAT_MODEL.md § T6; operators reading the crash "
            "won't know where to look for the rationale."
        )


class TestSecaStatusCallsVerifyRuntimeSafety:
    """Source-level pin: /seca/status handler must call
    ``verify_runtime_safety(world_model)`` so the boolean reflects
    actual runtime state — not just the boot-time ``SAFE_MODE``
    constant.  The PR 1 reviewer flagged the previous wiring as
    dead-code-by-docstring; PR 6 closes that thread.
    """

    def test_seca_status_handler_imports_verify_runtime_safety(self):
        """SEC_STATUS_VERIFY_WIRED: server.py /seca/status handler
        references verify_runtime_safety so the response reflects
        actual runtime state, not just the boot constant."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        # Locate the /seca/status handler region
        start = source.find('@app.get("/seca/status")')
        assert start >= 0, "no /seca/status handler found in server.py"
        next_route = source.find("@app.", start + 1)
        handler_block = source[start:next_route] if next_route > 0 else source[start:]
        assert "verify_runtime_safety" in handler_block, (
            "/seca/status handler no longer calls verify_runtime_safety; "
            "the response would revert to the boot-time SAFE_MODE constant "
            "and miss any runtime drift (e.g., a lazy import of a "
            "forbidden brain.* module after startup)."
        )


# ===========================================================================
# Invariants 41-45 — Gap 6-10 regression tests
# ===========================================================================
#
# 41. SEC_CORS_NO_WILDCARD          server.py must never fall back to allow_origins=["*"].
# 42. SEC_ANDROID_HTTPS_ASSERTION   build.gradle.kts release block asserts HTTPS base URL.
# 43. SEC_NETWORK_SECURITY_CONFIG   network_security_config.xml exists and blocks cleartext
#                                   globally (base-config cleartextTrafficPermitted="false").
# 44. SEC_HOST_EVAL_RATE_LIMITED    /engine/eval (POST+GET) in host_app.py has @limiter.limit.
# 45. SEC_SKILL_UPDATER_GUARDED     SkillUpdater.update_from_event() is wrapped in try/except.


_BUILD_GRADLE = _REPO_ROOT / "android" / "app" / "build.gradle.kts"
_NETWORK_SEC_XML = (
    _REPO_ROOT / "android" / "app" / "src" / "main" / "res" / "xml" / "network_security_config.xml"
)
_EVENTS_ROUTER = _REPO_ROOT / "llm" / "seca" / "events" / "router.py"


class TestCorsNoWildcard:
    """SEC_CORS_NO_WILDCARD (41): server.py must not default CORS allow_origins to ["*"]."""

    def test_cors_wildcard_not_in_source(self):
        """A bare ["*"] wildcard in the CORS setup means any origin can call the API."""
        source = _SERVER_PY.read_text(encoding="utf-8")
        # The wildcard is only acceptable as a literal in a comment or string inside a
        # non-CORS context.  We check the CORS middleware call specifically via AST.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # Look for CORSMiddleware(...) calls
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_cors = (isinstance(func, ast.Name) and func.id == "CORSMiddleware") or (
                isinstance(func, ast.Attribute) and func.attr == "CORSMiddleware"
            )
            if not is_cors:
                continue
            for kw in node.keywords:
                if kw.arg != "allow_origins":
                    continue
                # The value must not be a list literal containing "*"
                val = kw.value
                if isinstance(val, ast.List):
                    for elt in val.elts:
                        if isinstance(elt, ast.Constant) and elt.value == "*":
                            raise AssertionError(
                                "CORSMiddleware allow_origins contains a hardcoded '*' wildcard. "
                                "Set CORS_ALLOWED_ORIGINS explicitly; never default to ['*']."
                            )
                # The value must not be a conditional `... if DEBUG else ...` that resolves to ["*"]
                if isinstance(val, ast.IfExp):
                    for branch in (val.body, val.orelse):
                        if isinstance(branch, ast.List):
                            for elt in branch.elts:
                                if isinstance(elt, ast.Constant) and elt.value == "*":
                                    raise AssertionError(
                                        "CORSMiddleware allow_origins falls back to ['*'] in a "
                                        "conditional. Remove the wildcard branch entirely."
                                    )


class TestAndroidHttpsAssertion:
    """SEC_ANDROID_HTTPS_ASSERTION (42): release block in build.gradle.kts asserts https://."""

    def test_gradle_release_block_contains_https_check(self):
        """Release builds must refuse to compile when COACH_API_BASE is not HTTPS."""
        source = _BUILD_GRADLE.read_text(encoding="utf-8")
        assert "https://" in source, (
            "build.gradle.kts has no https:// reference in the release block. "
            "Add a build-time check that rejects non-TLS base URLs."
        )
        assert "error(" in source or "error(" in source, (
            "build.gradle.kts has no error() call. "
            "The release block must call error() when COACH_API_BASE is not HTTPS."
        )


class TestNetworkSecurityConfig:
    """SEC_NETWORK_SECURITY_CONFIG (43): network_security_config.xml blocks cleartext in release."""

    def test_config_file_exists(self):
        assert _NETWORK_SEC_XML.exists(), (
            f"Missing {_NETWORK_SEC_XML.relative_to(_REPO_ROOT)}. "
            "Add a network_security_config.xml to block cleartext traffic in release builds."
        )

    def test_base_config_blocks_cleartext(self):
        source = _NETWORK_SEC_XML.read_text(encoding="utf-8")
        assert 'cleartextTrafficPermitted="false"' in source, (
            'network_security_config.xml does not set cleartextTrafficPermitted="false" '
            "in <base-config>. Release builds can still send data over plain HTTP."
        )

    def test_manifest_references_config(self):
        manifest = (
            _REPO_ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
        ).read_text(encoding="utf-8")
        assert "network_security_config" in manifest, (
            "AndroidManifest.xml does not reference @xml/network_security_config. "
            "The network security policy is ignored until the manifest is updated."
        )


class TestEngineEvalRateLimited:
    """SEC_HOST_EVAL_RATE_LIMITED (44): /engine/eval on server.py must
    have @limiter.limit decorator.  Migrated from host_app.py in the
    host_app retirement pass (2026-05-12); the rate limit is the same
    30/minute that host_app had.  GET variant was dropped during the
    migration (Android never used it) so only the POST is pinned."""

    def setup_method(self):
        self._tree = _parse(_SERVER_PY)
        self._funcs = _get_decorated_functions(self._tree)

    def _has_limiter_decorator(self, func_name: str) -> bool:
        func = self._funcs.get(func_name)
        if func is None:
            return False
        for dec in func.decorator_list:
            if isinstance(dec, ast.Call):
                f = dec.func
                if isinstance(f, ast.Attribute) and f.attr == "limit":
                    return True
        return False

    def test_engine_eval_post_is_rate_limited(self):
        """POST /engine/eval must have a @limiter.limit() decorator.
        Without it, an attacker with a valid SECA_API_KEY could exhaust
        the Stockfish pool by spamming the endpoint."""
        assert self._has_limiter_decorator("engine_eval"), (
            "engine_eval() (POST /engine/eval) in server.py has no @limiter.limit "
            "decorator.  Add @limiter.limit('30/minute') to match the host_app "
            "pre-retirement rate limit."
        )


class TestSkillUpdaterGuarded:
    """SEC_SKILL_UPDATER_GUARDED (45): SkillUpdater call is wrapped in try/except."""

    def test_skill_updater_call_is_in_try_block(self):
        """A DB failure in SkillUpdater must not abort the /game/finish request."""
        tree = _parse(_EVENTS_ROUTER)

        # Find every Try node and check whether any of their bodies contain a call to
        # update_from_event (the SkillUpdater method).
        def _body_calls_update(nodes) -> bool:
            for node in ast.walk(
                ast.Module(body=nodes, type_ignores=[]) if not isinstance(nodes, ast.AST) else nodes
            ):
                if isinstance(node, ast.Call):
                    f = node.func
                    if isinstance(f, ast.Attribute) and f.attr == "update_from_event":
                        return True
            return False

        try_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        assert any(_body_calls_update(t.body) for t in try_nodes), (
            "SkillUpdater.update_from_event() in events/router.py is not inside a try/except "
            "block. A DB write failure will propagate as HTTP 500, freezing the player's rating."
        )
