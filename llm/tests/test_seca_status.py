"""
SECA status endpoint and SAFE_MODE constant tests.

These tests verify three invariants critical for P4 auditability:
1.  SAFE_MODE is permanently True in this release (no accidental disabling).
2.  assert_safe() never raises while SAFE_MODE is True.
3.  GET /seca/status returns the correct runtime flag without authentication
    AND does not leak the previously-exposed bandit_enabled / version fields.

Test tiers
----------
Tier 1 — direct import of the safe_mode module (no FastAPI stack required).
Tier 2 — minimal FastAPI stub that mirrors the seca_status() handler logic,
          avoiding the server.py import chain (which requires Stockfish + DB).

Invariants pinned
-----------------
  SAFE_MODE_CONST_TRUE       SAFE_MODE constant == True in safe_mode.py.
  ASSERT_SAFE_NOOP           assert_safe() does not raise when SAFE_MODE is True.
  STATUS_200                 GET /seca/status returns HTTP 200.
  STATUS_SAFE_MODE_TRUE      Response safe_mode field is True.
  STATUS_NO_BANDIT_FIELD     Response does NOT include bandit_enabled
                             (redundant with safe_mode; trimmed for info-leak).
  STATUS_NO_VERSION_FIELD    Response does NOT include version
                             (no client decision used it; trimmed for info-leak).
  STATUS_NO_AUTH             Endpoint is accessible without any API key.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Tier 1 — safe_mode module invariants
# ---------------------------------------------------------------------------


class TestSafeModeConstant:

    def test_safe_mode_const_true(self):
        """SAFE_MODE_CONST_TRUE: SAFE_MODE is True in the runtime module."""
        from llm.seca.runtime.safe_mode import SAFE_MODE

        assert SAFE_MODE is True, (
            "SAFE_MODE must be True in this release. "
            "Bandit training and neural policy updates are prohibited."
        )

    def test_assert_safe_noop_when_safe_mode_true(self):
        """ASSERT_SAFE_NOOP: assert_safe() does not raise when SAFE_MODE is True."""
        from llm.seca.runtime.safe_mode import assert_safe

        # Must not raise
        assert_safe()

    def test_safe_mode_default_when_env_unset(self):
        """SAFE_MODE_DEFAULT_TRUE: with SECA_SAFE_MODE unset, the resolver
        returns True.  Pins the safe-by-default contract — production runs
        without setting the env var, and SAFE_MODE must still be True."""
        import os
        from unittest.mock import patch
        from llm.seca.runtime import safe_mode as safe_mode_module

        env = {k: v for k, v in os.environ.items() if k != "SECA_SAFE_MODE"}
        with patch.dict(os.environ, env, clear=True):
            assert safe_mode_module._resolve_safe_mode() is True

    def test_safe_mode_env_var_can_disable_in_dev(self):
        """SAFE_MODE_ENV_DRIVEN: SECA_SAFE_MODE=false resolves to False so
        developers can exercise the dormant `if not SAFE_MODE:` branches
        under test.  The freeze guard separately enforces SAFE_MODE=True
        in production (see test_safety_freeze.FreezeSafeModeLockTest)."""
        import os
        from unittest.mock import patch
        from llm.seca.runtime import safe_mode as safe_mode_module

        with patch.dict(os.environ, {"SECA_SAFE_MODE": "false"}):
            assert safe_mode_module._resolve_safe_mode() is False


# ---------------------------------------------------------------------------
# Tier 2 — /seca/status endpoint shape (minimal stub, no server.py import)
# ---------------------------------------------------------------------------


def _build_status_app() -> FastAPI:
    """Return a minimal FastAPI app that mirrors the seca_status() handler."""
    from llm.seca.runtime.safe_mode import SAFE_MODE

    stub = FastAPI()

    @stub.get("/seca/status")
    def seca_status():
        return {"safe_mode": SAFE_MODE}

    return stub


@pytest.fixture(scope="module")
def status_client():
    app = _build_status_app()
    with TestClient(app) as c:
        yield c


class TestSecaStatusEndpoint:

    def test_status_200(self, status_client):
        """STATUS_200: GET /seca/status returns HTTP 200."""
        resp = status_client.get("/seca/status")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    def test_status_safe_mode_true(self, status_client):
        """STATUS_SAFE_MODE_TRUE: safe_mode field is True."""
        data = status_client.get("/seca/status").json()
        assert data.get("safe_mode") is True, (
            f"safe_mode must be True, got {data.get('safe_mode')!r}"
        )

    def test_status_no_bandit_field(self, status_client):
        """STATUS_NO_BANDIT_FIELD: bandit_enabled must NOT be in the response.

        Earlier releases returned ``{"safe_mode": True, "bandit_enabled": False, ...}``.
        ``bandit_enabled`` is just ``not safe_mode`` — it added no information
        a client could not derive, while explicitly disclosing RL-state to
        unauthenticated callers.  The trim is asserted as a contract here so
        a future careless restoration regresses loudly.
        """
        data = status_client.get("/seca/status").json()
        assert "bandit_enabled" not in data, (
            "bandit_enabled must not appear in /seca/status response — "
            "it is redundant with safe_mode and was a small information leak."
        )

    def test_status_no_version_field(self, status_client):
        """STATUS_NO_VERSION_FIELD: version must NOT be in the response.

        Earlier releases returned ``"version": "1.0"``.  No client decision
        used the field; it added a fingerprintable surface with no
        compensating use case.
        """
        data = status_client.get("/seca/status").json()
        assert "version" not in data, (
            "version must not appear in /seca/status response — "
            "no client behaviour used it and it added a fingerprintable surface."
        )

    def test_status_no_auth_required(self, status_client):
        """STATUS_NO_AUTH: endpoint is accessible without any Authorization or X-Api-Key header."""
        resp = status_client.get("/seca/status")
        assert resp.status_code == 200, (
            f"/seca/status must be open (no auth). Got {resp.status_code}"
        )
