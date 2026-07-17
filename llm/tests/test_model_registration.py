"""
Model-registration completeness — regression guard for the 2026-07-17
prod incident (MR_01..05).

Root cause pinned
-----------------
``Base.metadata.create_all`` only creates tables for models that are
IMPORTED when ``init_schema()`` runs.  ``llm/seca/auth/router.py``
carries the load-bearing wildcard import block for exactly this reason,
but ``llm.seca.brain.training.models`` was missing from it — harmless
for as long as nothing touched those tables at runtime, until the GDPR
erasure/export endpoints (which import the module lazily, at request
time, AFTER create_all) became their first live consumer.  Result:
``DELETE /auth/me`` and ``GET /auth/me/export`` 500'd in production
with ``no such table: training_outcomes`` while every SQLite unit test
passed (the test fixtures imported the erasure module BEFORE running
their own create_all, masking the gap).

Pinned invariants
-----------------
 1. MR_01 BLOCK_IS_COMPLETE       every llm/seca/**/models.py that defines a
                                  ``__tablename__`` appears as a wildcard
                                  import in auth/router.py — glob-driven, so
                                  the NEXT unregistered model module fails
                                  here, not in production.
 2. MR_02 FULL_APP_REGISTER       the real FastAPI app (lifespan →
                                  init_schema) serves POST /auth/register.
 3. MR_03 FULL_APP_EXPORT         GET /auth/me/export returns 200 through the
                                  full app, and the document contains the
                                  exact tables the incident was missing
                                  (training_decisions / training_outcomes).
 4. MR_04 FULL_APP_DELETE         DELETE /auth/me returns 200 through the
                                  full app.
 5. MR_05 TOKEN_DEAD_AFTER_DELETE the token that authorised the deletion
                                  401s afterwards.

MR_02..05 deliberately avoid importing the erasure/export modules at
collection time (importing them would register the models and mask the
exact gap this file exists to catch); the expected table names are
hardcoded strings.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ROUTER_PATH = PROJECT_ROOT / "llm" / "seca" / "auth" / "router.py"


class TestRegistrationBlockCompleteness:
    def test_every_table_defining_models_module_is_registered(self):
        """MR_01: glob llm/seca/**/models.py; every file that defines a
        __tablename__ must be wildcard-imported by auth/router.py."""
        router_source = ROUTER_PATH.read_text(encoding="utf-8")
        missing = []
        for path in sorted((PROJECT_ROOT / "llm" / "seca").rglob("models.py")):
            source = path.read_text(encoding="utf-8")
            if "__tablename__" not in source:
                continue  # re-export shims define no tables
            rel = path.relative_to(PROJECT_ROOT).with_suffix("")
            module = ".".join(rel.parts)
            pattern = re.compile(rf"^from {re.escape(module)} import \*", re.MULTILINE)
            if not pattern.search(router_source):
                missing.append(module)
        assert not missing, (
            f"Model modules not registered in auth/router.py's wildcard "
            f"import block: {missing}.  Without the import, create_all "
            "never creates their tables and the first runtime consumer "
            "500s with 'no such table' (2026-07-17 prod incident)."
        )


@pytest.fixture(scope="module")
def app_client():
    """The REAL FastAPI app with lifespan (init_schema) — the surface the
    unit-test fixtures bypass."""
    from fastapi.testclient import TestClient

    import llm.server as server_module

    with TestClient(server_module.app) as client:
        yield client


class TestRightsEndpointsThroughFullApp:
    HEADERS = {"X-API-Version": "1"}

    def _register(self, app_client) -> str:
        email = f"mr-fullapp-{uuid.uuid4().hex[:12]}@example.com"
        r = app_client.post(
            "/auth/register",
            json={"email": email, "password": "mr-fullapp-pass-123"},
            headers=self.HEADERS,
        )
        assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
        return r.json()["access_token"]

    def test_export_and_delete_through_the_real_app(self, app_client):
        """MR_02 + MR_03 + MR_04 + MR_05 in one journey (one account)."""
        token = self._register(app_client)
        auth = {"Authorization": f"Bearer {token}", **self.HEADERS}

        r = app_client.get("/auth/me/export", headers=auth)
        assert r.status_code == 200, f"export failed: {r.status_code} {r.text[:300]}"
        data = r.json()["data"]
        # The exact tables the incident was missing — hardcoded on
        # purpose (importing ERASED_TABLES here would register the
        # models at collection time and mask the gap).
        assert "training_decisions" in data
        assert "training_outcomes" in data
        assert "players" in data and len(data["players"]) == 1

        r = app_client.request("DELETE", "/auth/me", headers=auth)
        assert r.status_code == 200, f"delete failed: {r.status_code} {r.text[:300]}"
        assert r.json() == {"status": "deleted"}

        r = app_client.get("/auth/me", headers=auth)
        assert r.status_code == 401, "token must be dead after account deletion"
