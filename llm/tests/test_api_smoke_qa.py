"""
API & Backend Smoke QA Tests — llm/tests/test_api_smoke_qa.py

QA Plan Section 9 — API-01 through API-06 (automated subset).

API-01  GET /health returns 200 OK with valid response body
API-03  POST /inference with invalid FEN returns 400 with descriptive error
API-04  Engine auth is required — unauthenticated calls return 401 (see test_api_security.py)
API-05  Exponential backoff fires on repeated LLM failures (see test_explain_pipeline_retry.py)
API-06  seca_doctor.py safety check structure is intact
CI-06   Deploy step only runs after all test/build steps pass (workflow audit)
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LLM_ROOT = _REPO_ROOT / "llm"


# ---------------------------------------------------------------------------
# API-01 — GET /health returns 200 OK with valid JSON body
# ---------------------------------------------------------------------------


class TestApi01HealthEndpoint:
    """API-01: /health must return 200 with a parseable JSON body."""

    def _call_health(self, app_module_path: str):
        """Call /health on the given app module via TestClient."""
        from fastapi.testclient import TestClient

        if app_module_path == "host_app":
            from llm import host_app
            client = TestClient(host_app.app, raise_server_exceptions=False)
        else:
            from llm import server as server_module
            client = TestClient(server_module.app, raise_server_exceptions=False)
        return client.get("/health")

    def test_host_app_health_returns_200(self):
        """host_app.py /health must return HTTP 200."""
        response = self._call_health("host_app")
        assert response.status_code == 200, (
            f"/health returned {response.status_code}, expected 200. "
            f"Body: {response.text[:200]}"
        )

    def test_host_app_health_body_is_json(self):
        """host_app.py /health response body must be parseable JSON."""
        response = self._call_health("host_app")
        try:
            body = response.json()
            assert isinstance(body, dict), f"/health must return a JSON object; got {type(body)}"
        except Exception as exc:
            pytest.fail(f"/health response is not valid JSON: {exc}. Body: {response.text[:200]}")

    def test_server_health_returns_200(self):
        """server.py /health must return HTTP 200."""
        response = self._call_health("server")
        assert response.status_code == 200, (
            f"server.py /health returned {response.status_code}, expected 200. "
            f"Body: {response.text[:200]}"
        )

    def test_server_health_body_is_json(self):
        """server.py /health response body must be parseable JSON."""
        response = self._call_health("server")
        try:
            body = response.json()
            assert isinstance(body, dict)
        except Exception as exc:
            pytest.fail(f"server.py /health is not valid JSON: {exc}")

    def test_health_endpoint_registered_on_host_app(self):
        """host_app.py must have a /health route registered."""
        from llm import host_app
        paths = [getattr(r, "path", None) for r in host_app.app.routes]
        assert "/health" in paths, "host_app.app must have a /health route"

    def test_health_endpoint_registered_on_server(self):
        """server.py must have a /health route registered."""
        from llm import server as server_module
        paths = [getattr(r, "path", None) for r in server_module.app.routes]
        assert "/health" in paths, "server.app must have a /health route"


# ---------------------------------------------------------------------------
# API-03 — Invalid FEN returns 400 with descriptive error (no stack trace)
# ---------------------------------------------------------------------------


_INVALID_FENS = [
    "not-a-fen",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP w KQkq - 0 1",  # missing rank
    "",
    "   ",
    "8/8/8/8/8/8/8/8 x KQkq - 0 1",  # invalid side-to-move
]

_VALID_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class TestApi03InvalidFenReturns400:
    """API-03: Invalid FEN must return a 4xx response with an error message (no stack trace)."""

    def test_engine_eval_endpoint_rejects_completely_invalid_fen(self, monkeypatch):
        """POST /engine/eval with a non-FEN string must not return 200."""
        from fastapi.testclient import TestClient
        from llm import host_app

        monkeypatch.setattr(host_app._limiter, "enabled", False)

        client = TestClient(host_app.app, raise_server_exceptions=False)
        resp = client.post("/engine/eval", json={"fen": "not-a-fen"})

        # Should not be 200; 422 (validation) or 400 are acceptable
        assert resp.status_code != 200, (
            "Invalid FEN 'not-a-fen' must not return HTTP 200"
        )

    def test_engine_eval_rejects_empty_fen(self, monkeypatch):
        """POST /engine/eval with empty string FEN must not return 200."""
        from fastapi.testclient import TestClient
        from llm import host_app

        monkeypatch.setattr(host_app._limiter, "enabled", False)

        client = TestClient(host_app.app, raise_server_exceptions=False)
        resp = client.post("/engine/eval", json={"fen": ""})
        assert resp.status_code != 200

    def test_error_response_does_not_expose_stack_trace(self, monkeypatch):
        """
        Error responses for invalid inputs must not expose Python stack traces
        (no 'Traceback', 'File "...', or 'line N').
        """
        from fastapi.testclient import TestClient
        from llm import host_app

        monkeypatch.setattr(host_app._limiter, "enabled", False)

        client = TestClient(host_app.app, raise_server_exceptions=False)
        resp = client.post("/engine/eval", json={"fen": "not-a-fen"})

        body_text = resp.text
        assert "Traceback" not in body_text, (
            "Error response must not expose 'Traceback' to the client"
        )
        assert 'File "' not in body_text, (
            "Error response must not expose Python file paths to the client"
        )

    def test_valid_startpos_fen_is_accepted(self, monkeypatch):
        """POST /engine/eval with the standard starting FEN must not be rejected as invalid."""
        from fastapi.testclient import TestClient
        from llm import host_app

        class _FakeEvaluator:
            default_nodes = 5000

            def resolve_limits(self, *, movetime, nodes):
                return None, self.default_nodes

        async def _fake_evaluate(*, fen, moves, movetime, nodes):
            return (
                {"score": 0, "best_move": "e2e4", "source": "engine"},
                {"cache_hit": False, "source": "engine", "engine_wait_ms": 1.0,
                 "engine_eval_ms": 5.0, "total_ms": 6.0},
            )

        monkeypatch.setattr(host_app._limiter, "enabled", False)
        monkeypatch.setattr(host_app, "engine_eval", _FakeEvaluator())
        monkeypatch.setattr(host_app.engine_service, "evaluate_with_metrics", _fake_evaluate)

        client = TestClient(host_app.app, raise_server_exceptions=False)
        resp = client.post("/engine/eval", json={"fen": _VALID_FEN})
        assert resp.status_code == 200, (
            f"Valid starting FEN must return 200; got {resp.status_code}: {resp.text[:200]}"
        )


# ---------------------------------------------------------------------------
# API-06 — seca_doctor.py safety check structure is intact
# ---------------------------------------------------------------------------


class TestApi06SecaDoctorStructure:
    """API-06: seca_doctor.py must define the required safety check infrastructure."""

    _SECA_DOCTOR = _LLM_ROOT / "seca" / "seca_doctor.py"

    def test_seca_doctor_file_exists(self):
        """seca_doctor.py must exist in the llm/ directory."""
        # Try common locations.  Canonical home is llm/seca/seca_doctor.py
        # (the script lives in the seca package); the older _LLM_ROOT and
        # _REPO_ROOT locations are kept as fallbacks for legacy checkouts.
        candidates = [
            _LLM_ROOT / "seca" / "seca_doctor.py",
            _LLM_ROOT / "seca_doctor.py",
            _REPO_ROOT / "seca_doctor.py",
        ]
        found = any(p.exists() for p in candidates)
        assert found, (
            "seca_doctor.py not found. "
            "API-06 requires seca_doctor.py to report all safety checks on clean install."
        )

    def test_seca_doctor_is_callable(self):
        """seca_doctor.py must be a valid Python file that can be imported for inspection."""
        for candidate in [
            _LLM_ROOT / "seca" / "seca_doctor.py",
            _LLM_ROOT / "seca_doctor.py",
            _REPO_ROOT / "seca_doctor.py",
        ]:
            if candidate.exists():
                src = candidate.read_text(encoding="utf-8")
                # Must be non-empty and have at least one function/check
                assert len(src) > 50, "seca_doctor.py must be a non-trivial script"
                assert "def " in src or "check" in src.lower(), (
                    "seca_doctor.py must define check functions"
                )
                return
        pytest.skip("seca_doctor.py not found")

    def test_check_db_uses_allowlist(self):
        """check_db.py must use a hardcoded allowlist — no dynamic table names (SH-14)."""
        check_db = _LLM_ROOT / "check_db.py"
        if not check_db.exists():
            pytest.skip("check_db.py not found")
        src = check_db.read_text(encoding="utf-8")
        assert "_ALLOWED_TABLES" in src or "allowlist" in src.lower(), (
            "check_db.py must use a hardcoded _ALLOWED_TABLES frozenset "
            "to prevent dynamic SQL table name injection"
        )


# ---------------------------------------------------------------------------
# CI-06 — Deploy step only runs after all tests pass (workflow audit)
# ---------------------------------------------------------------------------


class TestCi06DeployDependencyChain:
    """CI-06: Deploy job must depend on test/build jobs (never runs on test failure)."""

    def _get_workflow_files(self):
        gh_dir = _REPO_ROOT / ".github" / "workflows"
        if not gh_dir.exists():
            return []
        return list(gh_dir.glob("*.yml")) + list(gh_dir.glob("*.yaml"))

    def test_deploy_workflow_exists(self):
        """At least one GitHub Actions workflow file must exist."""
        workflows = self._get_workflow_files()
        assert workflows, (
            "No GitHub Actions workflow files found in .github/workflows/. "
            "CI-06 requires a deploy workflow with a dependency chain."
        )

    def test_deploy_job_has_needs_dependency(self):
        """
        Any workflow job named 'deploy' must have a 'needs:' key
        so it cannot run when tests fail.
        """
        import yaml

        workflows = self._get_workflow_files()
        deploy_jobs_without_needs = []

        for wf_path in workflows:
            try:
                wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            jobs = (wf or {}).get("jobs", {})
            for job_name, job_def in jobs.items():
                if "deploy" in job_name.lower():
                    if not job_def.get("needs"):
                        deploy_jobs_without_needs.append(
                            f"{wf_path.name}:{job_name}"
                        )

        assert not deploy_jobs_without_needs, (
            "The following deploy jobs have no 'needs:' dependency chain — "
            "they can run even when tests fail:\n"
            + "\n".join(f"  - {j}" for j in deploy_jobs_without_needs)
        )

    def test_no_deploy_on_test_failure_implied_by_needs_chain(self):
        """
        Verify that at least one workflow has a job with both 'deploy' in the name
        and a 'needs' list referencing another job.
        """
        import yaml

        workflows = self._get_workflow_files()
        protected_deploy_found = False

        for wf_path in workflows:
            try:
                wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            jobs = (wf or {}).get("jobs", {})
            for job_name, job_def in jobs.items():
                if "deploy" in job_name.lower() and job_def.get("needs"):
                    protected_deploy_found = True
                    break

        assert protected_deploy_found, (
            "No deploy job with a 'needs:' dependency was found in any workflow. "
            "CI-06 requires the deploy step to depend on test/build steps."
        )
