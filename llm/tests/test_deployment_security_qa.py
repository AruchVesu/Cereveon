"""
Deployment & Security QA Tests — llm/tests/test_deployment_security_qa.py

QA Plan Sections 10–11 — DEP and HTZ automated subset.

DEP-03  Environment variables (API keys, secrets) are NOT hardcoded in Dockerfile
DEP-06  verify_safety.py / safety infrastructure passes the SECA safety rules
HTZ-20  DB credentials are sourced from env vars — not hardcoded in any config file
HTZ-23  No secrets or API keys detected in image (source-level scan)
HTZ-28  Ollama model version is pinned — not floating to 'latest'
SEC     No f-string SQL interpolation (extends SH-15 from test_security_hardening.py)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LLM_ROOT = _REPO_ROOT / "llm"

# ---------------------------------------------------------------------------
# Secret patterns to scan for in source files
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    # Hard-coded API keys / tokens
    re.compile(r'(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token)\s*=\s*["\'][^"\']{8,}["\']'),
    # Hard-coded passwords
    re.compile(r'(?i)password\s*=\s*["\'][^"\']{6,}["\']'),
    # Postgres DSN with credentials inline
    re.compile(r'postgres(?:ql)?://\w+:\w+@'),
    # AWS-style access keys
    re.compile(r'AKIA[0-9A-Z]{16}'),
    # Private key PEM headers
    re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'),
]

_DOCKERFILE_PATHS = [
    _LLM_ROOT / "Dockerfile",
    _LLM_ROOT / "Dockerfile.api",
    _REPO_ROOT / "Dockerfile",
]

_SOURCE_DIRS_TO_SCAN = [
    _LLM_ROOT / "seca",
    _LLM_ROOT,
]


# ---------------------------------------------------------------------------
# DEP-03 / HTZ-23 — No secrets hardcoded in Dockerfiles
# ---------------------------------------------------------------------------


class TestDep03NoSecretsInDockerfile:
    """DEP-03/HTZ-23: API keys and secrets must NEVER be hardcoded in Dockerfiles."""

    def _scan_dockerfile(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        violations = []
        for pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line_num = text[: match.start()].count("\n") + 1
                violations.append(f"  Line {line_num}: {match.group(0)!r}")
        return violations

    @pytest.mark.parametrize(
        "dockerfile_path",
        [p for p in _DOCKERFILE_PATHS],
        ids=[p.name for p in _DOCKERFILE_PATHS],
    )
    def test_dockerfile_has_no_hardcoded_secrets(self, dockerfile_path: Path):
        """No Dockerfile must contain hardcoded API keys, passwords, or tokens."""
        if not dockerfile_path.exists():
            pytest.skip(f"{dockerfile_path.name} not found — skipping")
        violations = self._scan_dockerfile(dockerfile_path)
        assert not violations, (
            f"{dockerfile_path.name} contains potential hardcoded secrets:\n"
            + "\n".join(violations)
        )

    def test_dockerfile_uses_env_for_api_key(self):
        """LLM Dockerfile must not hardcode SECA_API_KEY — must use ARG or ENV from runtime."""
        llm_dockerfile = _LLM_ROOT / "Dockerfile"
        if not llm_dockerfile.exists():
            pytest.skip("llm/Dockerfile not found")
        text = llm_dockerfile.read_text(encoding="utf-8")
        # Must not hardcode the key value
        assert "SECA_API_KEY=" not in text or "SECA_API_KEY=$" in text or \
               re.search(r'SECA_API_KEY\s*=\s*["\']?\$', text), (
            "Dockerfile must not hardcode SECA_API_KEY — use ENV from runtime secrets"
        )

    def test_dockerfile_does_not_copy_env_file(self):
        """Dockerfile must not COPY .env files (secrets must never enter image layers)."""
        for dockerfile_path in _DOCKERFILE_PATHS:
            if not dockerfile_path.exists():
                continue
            text = dockerfile_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("COPY") and ".env" in stripped and not stripped.startswith("#"):
                    # COPY .env.example is allowed; COPY .env is not
                    if ".env.example" not in stripped:
                        pytest.fail(
                            f"{dockerfile_path.name} line {i} copies a .env file into the image: "
                            f"{stripped!r}\n"
                            "Secrets must never be baked into Docker image layers."
                        )

    def test_docker_compose_does_not_hardcode_secrets(self):
        """docker-compose.yml must not hardcode secret values inline."""
        compose_paths = [
            _REPO_ROOT / "docker-compose.yml",
            _REPO_ROOT / "docker-compose.yaml",
        ]
        for compose_path in compose_paths:
            if not compose_path.exists():
                continue
            text = compose_path.read_text(encoding="utf-8")
            for pattern in _SECRET_PATTERNS:
                match = pattern.search(text)
                if match:
                    line_num = text[: match.start()].count("\n") + 1
                    pytest.fail(
                        f"{compose_path.name} line {line_num} appears to contain a hardcoded secret: "
                        f"{match.group(0)!r}\n"
                        "Use ${VAR_NAME} references instead."
                    )


# ---------------------------------------------------------------------------
# HTZ-20 — DB credentials sourced from env vars, not hardcoded
# ---------------------------------------------------------------------------


class TestHtz20DbCredentialsFromEnvVars:
    """HTZ-20: Database credentials must always come from environment variables."""

    def _python_files_in(self, directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return [
            p for p in directory.rglob("*.py")
            if "venv" not in str(p) and "__pycache__" not in str(p)
        ]

    def test_no_hardcoded_postgres_dsn_with_credentials(self):
        """No Python source file in llm/seca must hardcode a Postgres DSN with password."""
        seca_files = self._python_files_in(_LLM_ROOT / "seca")
        pattern = re.compile(r'postgres(?:ql)?://\w+:\w+@(?!localhost)')
        violations = []
        for path in seca_files:
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                violations.append(str(path.relative_to(_REPO_ROOT)))
        assert not violations, (
            "Hardcoded Postgres DSN with credentials found in:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_db_init_does_not_hardcode_credentials(self):
        """
        The DB initialisation code (SQLite) must not hardcode connection credentials.
        For SQLite the path is fixed by design and volume-mounted (HTZ-19);
        what matters is that no passwords are embedded in source.
        """
        db_file = _LLM_ROOT / "seca" / "storage" / "db.py"
        if not db_file.exists():
            pytest.skip("llm/seca/storage/db.py not found")
        src = db_file.read_text(encoding="utf-8")
        # SQLite has no credentials — verify no Postgres DSN is accidentally hardcoded
        assert not re.search(r'postgresql?://[^:]+:[^@]+@', src), (
            "db.py must not hardcode a Postgres DSN with embedded credentials"
        )
        # The db file must reference its own path constant or configuration
        assert "DB_PATH" in src or "seca.db" in src or "database" in src.lower(), (
            "db.py must reference its database path"
        )


# ---------------------------------------------------------------------------
# DEP-06 — Safety verification infrastructure is present and structurally valid
# ---------------------------------------------------------------------------


class TestDep06SafetyVerification:
    """DEP-06: The SECA safety verification infrastructure must be intact and passing."""

    def test_safe_mode_constant_is_true(self):
        """SAFE_MODE must be True — autonomous RL is prohibited (Project Rule 3)."""
        from llm.seca.runtime.safe_mode import SAFE_MODE

        assert SAFE_MODE is True, (
            "SAFE_MODE must be True. "
            "DEP-06 requires that the safety checks pass in the containerized environment."
        )

    def test_safety_freeze_module_exists(self):
        """The safety.freeze module must exist and expose an 'enforce' function."""
        try:
            from llm.seca.safety.freeze import enforce
            assert callable(enforce), "enforce must be callable"
        except ImportError as exc:
            pytest.fail(f"llm.seca.safety.freeze could not be imported: {exc}")

    def test_validate_output_raises_on_forbidden_phrase(self):
        """The core output validator must block forbidden phrases (safety gate is active)."""
        from llm.rag.contracts.validate_output import validate_output

        with pytest.raises(AssertionError):
            validate_output("Stockfish says this is the best move.", case_type="normal")

    def test_validate_mode_2_negative_raises_on_move_suggestion(self):
        """The Mode-2 negative validator must block move suggestions."""
        from llm.rag.validators.mode_2_negative import validate_mode_2_negative

        with pytest.raises(AssertionError):
            validate_mode_2_negative("The engine should play Nf3 here.")

    def test_fake_llm_compliant_output_passes_validators(self):
        """The compliant FakeLLM output must pass all validators (safety baseline)."""
        from llm.rag.llm.fake import FakeLLM
        from llm.rag.llm.run_mode_2 import run_mode_2

        llm = FakeLLM(mode="compliant")
        # Must not raise
        run_mode_2(llm=llm, prompt="dummy prompt", case_type="forced_mate")

    def test_fake_llm_forbidden_phrase_fails_validators(self):
        """The 'forbidden_phrase' FakeLLM mode must be caught by validators."""
        from llm.rag.llm.fake import FakeLLM
        from llm.rag.llm.run_mode_2 import run_mode_2

        llm = FakeLLM(mode="forbidden_phrase")
        with pytest.raises(AssertionError):
            run_mode_2(llm=llm, prompt="dummy prompt", case_type="forced_mate")

    def test_missing_data_violation_fails_validators(self):
        """FakeLLM missing_data_violation must be caught."""
        from llm.rag.llm.fake import FakeLLM
        from llm.rag.llm.run_mode_2 import run_mode_2

        llm = FakeLLM(mode="missing_data_violation")
        with pytest.raises(AssertionError):
            run_mode_2(llm=llm, prompt="dummy prompt", case_type="missing_data")


# ---------------------------------------------------------------------------
# HTZ-28 — Ollama model version is pinned (not floating to 'latest')
# ---------------------------------------------------------------------------


class TestHtz28OllamaModelPinned:
    """HTZ-28: Ollama model must be pinned to a specific version tag, not 'latest'."""

    def _find_model_config_files(self) -> list[Path]:
        candidates = []
        for pattern in ["docker-compose.yml", "docker-compose.yaml", ".env.example"]:
            p = _REPO_ROOT / pattern
            if p.exists():
                candidates.append(p)
        return candidates

    def test_env_example_does_not_set_ollama_model_to_latest(self):
        """The .env.example must not set COACH_OLLAMA_MODEL=latest."""
        env_example = _REPO_ROOT / ".env.example"
        if not env_example.exists():
            pytest.skip(".env.example not found")
        text = env_example.read_text(encoding="utf-8")
        assert "COACH_OLLAMA_MODEL=latest" not in text, (
            ".env.example must not set COACH_OLLAMA_MODEL=latest. "
            "Pin the model to a specific version tag (e.g., qwen2.5:7b-instruct-q2_K)."
        )

    def test_ollama_model_name_contains_version_info(self):
        """The default Ollama model name must contain a version qualifier (not just a base name)."""
        env_example = _REPO_ROOT / ".env.example"
        if not env_example.exists():
            pytest.skip(".env.example not found")
        text = env_example.read_text(encoding="utf-8")
        # Find the model name
        match = re.search(r'COACH_OLLAMA_MODEL\s*=\s*(\S+)', text)
        if not match:
            pytest.skip("COACH_OLLAMA_MODEL not found in .env.example")
        model_name = match.group(1).strip().strip('"\'')
        assert ":" in model_name, (
            f"COACH_OLLAMA_MODEL='{model_name}' has no version tag. "
            "Ollama model must be pinned with a ':version' suffix to prevent drift."
        )
        assert model_name.split(":")[1] != "latest", (
            f"COACH_OLLAMA_MODEL='{model_name}' uses 'latest'. "
            "Pin to a specific version to prevent automatic model drift."
        )


# ---------------------------------------------------------------------------
# General — No hardcoded secrets in core Python source files
# ---------------------------------------------------------------------------


class TestNoHardcodedSecretsInSource:
    """Cross-cutting: no hardcoded secrets in any Python source file."""

    _EXCLUDED_DIRS = {"venv", "__pycache__", ".git", "node_modules"}
    _EXCLUDED_TEST_PATTERNS = [
        # Test files may use the literal string "dev-key" as a fixture value
        re.compile(r'def test_'),
        re.compile(r'pytest\.fixture'),
    ]

    def _collect_source_files(self) -> list[Path]:
        files = []
        for path in _LLM_ROOT.rglob("*.py"):
            parts = set(path.parts)
            if parts & self._EXCLUDED_DIRS:
                continue
            files.append(path)
        return files

    def test_no_aws_access_keys_in_source(self):
        """No Python source file must contain an AWS-format access key."""
        pattern = re.compile(r'AKIA[0-9A-Z]{16}')
        violations = []
        for path in self._collect_source_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                violations.append(str(path.relative_to(_REPO_ROOT)))
        assert not violations, (
            "AWS access key pattern found in source files:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_private_key_pem_in_source(self):
        """No Python source file must contain a PEM private key."""
        pattern = re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----')
        violations = []
        for path in self._collect_source_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                violations.append(str(path.relative_to(_REPO_ROOT)))
        assert not violations, (
            "PEM private key header found in source files:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_hardcoded_postgres_credentials_in_source(self):
        """No Python source file must contain a Postgres URI with embedded password."""
        pattern = re.compile(r'postgresql?://[^:]+:[^@]+@')
        violations = []
        for path in self._collect_source_files():
            # Skip test files that might use db URIs for test fixtures
            if "test_" in path.name:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                violations.append(str(path.relative_to(_REPO_ROOT)))
        assert not violations, (
            "Hardcoded Postgres credentials found in source files:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
