"""Run the regression pipeline grouped by category.

Each group is run as a separate pytest invocation so that failures are
isolated and clearly attributed to the responsible category.  The script
exits non-zero as soon as the first group fails, mirroring the behaviour
of a typical CI gate.

Usage::

    python llm/run_regression_suite.py

Groups (in execution order):
  1. Engine regression         — engine evaluation, caching, limits, benchmark
  2. Coaching pipeline         — determinism, chat, and LLM contract tests
  3. API contract & security   — output validation, schema, and security audit
  4. Analysis pipeline         — historical pipeline and mistake analytics
  5. Layer boundaries          — architecture import-contract tests
  6. Golden tests              — retriever and prompt snapshots (Category A)
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Regression groups — order matters: cheapest/fastest catch first.
# ---------------------------------------------------------------------------
REGRESSION_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Engine regression",
        [
            # Surviving engine-area regressions after the engine-library
            # cleanup (2026-05-12): the flat engine_eval / engine_pool /
            # elite_engine_service modules were retired alongside host_app
            # (PR #111).  Live coverage now lives in test_engine_pool_*.py
            # against ``llm.seca.engines.stockfish.pool``.
            "llm/tests/test_engine_response_format.py",
            "llm/tests/test_engine_pool_evaluate_position.py",
            "llm/tests/test_engine_pool_exhaustion.py",
            "llm/tests/test_engine_pool_crash_recovery.py",
            "llm/tests/test_fen_move_cache_key.py",
            "llm/tests/test_stockfish_adapter_isolation.py",
        ],
    ),
    (
        "Coaching pipeline determinism",
        [
            "llm/tests/test_coaching_pipeline_regression.py",
            "llm/tests/test_chat_pipeline.py",
            "llm/rag/tests/contracts/test_fake_llm.py",
            "llm/rag/tests/contracts/test_mode_2_output.py",
        ],
    ),
    (
        "API contract and security",
        [
            "llm/tests/test_api_contract_validation.py",
            "llm/tests/test_api_security.py",
            "llm/tests/test_security_host_header_poisoning.py",
            "llm/tests/test_explain_schema_validation.py",
            "llm/rag/tests/test_run_mode_2_additional.py",
            "llm/rag/tests/test_run_mode_2_cascades.py",
            "llm/rag/tests/test_run_mode_2_mate_sanitization.py",
        ],
    ),
    (
        "Analysis pipeline",
        [
            "llm/tests/test_historical_pipeline.py",
            "llm/tests/test_mistake_analytics.py",
        ],
    ),
    (
        "Layer boundaries",
        [
            "llm/tests/test_seca_layer_boundaries.py",
        ],
    ),
    (
        "Golden tests",
        [
            "llm/rag/tests/golden/test_retriever.py",
            "llm/rag/tests/golden/test_prompt_snapshot.py",
        ],
    ),
]


def _run_group(label: str, targets: list[str]) -> int:
    """Run one regression group and return the pytest exit code."""
    separator = "=" * 60
    print(f"\n{separator}")
    print(f"  REGRESSION GROUP: {label}")
    print(separator)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        *targets,
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)
    return result.returncode


def main() -> int:
    print("=== REGRESSION PIPELINE ===")
    print(f"Groups: {len(REGRESSION_GROUPS)}")

    failed: list[str] = []
    for label, targets in REGRESSION_GROUPS:
        code = _run_group(label, targets)
        if code != 0:
            failed.append(label)
            # Fail fast: stop at first broken group so the error is clear.
            break

    print("\n" + "=" * 60)
    if failed:
        print(f"REGRESSION PIPELINE FAILED — broken group: {failed[0]}")
        return 1

    print(f"REGRESSION PIPELINE PASSED — {len(REGRESSION_GROUPS)} groups OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
