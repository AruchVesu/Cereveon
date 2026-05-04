"""Run the stable linting and type-check gates used by CI."""

from __future__ import annotations

from pathlib import Path
import argparse
import os
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYLINT_HOME = PROJECT_ROOT / "tmp_logs" / ".pylint"

FORMAT_TARGETS = [
    "scripts/run_all_tests.py",
    "llm/cache_keys.py",
    "llm/elite_engine_service.py",
    "llm/engine_eval.py",
    "llm/fen_hash.py",
    "llm/metrics.py",
    "llm/position_input.py",
    "llm/predictive_cache.py",
    "llm/run_all_tests.py",
    "llm/run_ci_suite.py",
    "llm/run_quality_gate.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/rag/contracts/validate_output.py",
    "llm/rag/llm/config.py",
    "llm/rag/llm/fake.py",
    "llm/rag/quality/explanation_score.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/validators/mode_2_negative.py",
    "llm/rag/validators/mode_2_structure.py",
    "llm/rag/validators/sanitize.py",
    "llm/tests/test_engine_eval_limits.py",
    "llm/tests/test_predictive_cache.py",
]

PYLINT_TARGETS = [
    "scripts/run_all_tests.py",
    "llm/cache_keys.py",
    "llm/elite_engine_service.py",
    "llm/engine_eval.py",
    "llm/fen_hash.py",
    "llm/metrics.py",
    "llm/position_input.py",
    "llm/predictive_cache.py",
    "llm/run_all_tests.py",
    "llm/run_ci_suite.py",
    "llm/run_quality_gate.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/rag/contracts/validate_output.py",
    "llm/rag/llm/config.py",
    "llm/rag/llm/fake.py",
    "llm/rag/quality/explanation_score.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/validators/mode_2_negative.py",
    "llm/rag/validators/mode_2_structure.py",
    "llm/rag/validators/sanitize.py",
]

MYPY_TARGETS = [
    "scripts/run_all_tests.py",
    "llm/fen_hash.py",
    "llm/metrics.py",
    "llm/position_input.py",
    "llm/run_all_tests.py",
    "llm/run_ci_suite.py",
    "llm/run_quality_gate.py",
    "llm/tests/test_ci_pipeline.py",
    "llm/rag/contracts/validate_output.py",
    "llm/rag/llm/config.py",
    "llm/rag/llm/fake.py",
    "llm/rag/quality/explanation_score.py",
    "llm/rag/tests/contracts/test_violations_corpus.py",
    "llm/rag/validators/mode_2_negative.py",
    "llm/rag/validators/mode_2_structure.py",
    "llm/rag/validators/sanitize.py",
]


def run_step(name: str, cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    print(f"=== RUNNING {name.upper()} ===")
    return subprocess.run(cmd, cwd=PROJECT_ROOT, check=False, env=env).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "steps",
        nargs="*",
        metavar="step",
        help="Run only the selected quality steps.",
    )
    args = parser.parse_args()

    PYLINT_HOME.mkdir(parents=True, exist_ok=True)

    pylint_env = os.environ.copy()
    pylint_env["PYLINTHOME"] = str(PYLINT_HOME)

    steps = [
        (
            "black",
            [sys.executable, "-m", "black", "--check", *FORMAT_TARGETS],
            None,
        ),
        (
            "pylint",
            [sys.executable, "-m", "pylint", "--score=n", *PYLINT_TARGETS],
            pylint_env,
        ),
        (
            "mypy",
            [sys.executable, "-m", "mypy", *MYPY_TARGETS],
            None,
        ),
    ]

    selected_steps = set(args.steps or [])
    invalid_steps = sorted(selected_steps - {"black", "pylint", "mypy"})
    if invalid_steps:
        parser.error(
            "invalid quality step(s): "
            + ", ".join(invalid_steps)
            + ". Choose from: black, pylint, mypy."
        )

    if selected_steps:
        steps = [step for step in steps if step[0] in selected_steps]

    for name, cmd, env in steps:
        code = run_step(name, cmd, env=env)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
