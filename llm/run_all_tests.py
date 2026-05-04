"""
Unified pytest runner for ChessCoach-AI.

Usage:
  python run_all_tests.py              -> CI-safe tests only
  python run_all_tests.py --local      -> CI + local-only tests
  python run_all_tests.py --llm        -> ONLY real-LLM tests
"""

from pathlib import Path
import subprocess
import sys

LLM_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = LLM_ROOT.parent

CI_TESTS = [
    "llm/rag/tests/golden/test_retriever.py",
    "llm/rag/tests/golden/test_prompt_snapshot.py",
    "llm/rag/tests/contracts/test_fake_llm.py",
]

LOCAL_ONLY_TESTS = [
    "llm/rag/tests/llm/test_ollama_smoke.py",
    "llm/rag/tests/llm/test_llm_regression.py",
]

QUALITY_TESTS = [
    "llm/rag/tests/quality/test_explanation_quality.py",
]


def run(paths: list[str], label: str) -> None:
    cmd = [sys.executable, "-m", "pytest", "-q", *paths]
    print(f"\n=== RUNNING {label}: {' '.join(paths)} ===")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        print(f"\nFAILED: {label}")
        sys.exit(result.returncode)
    print(f"PASSED: {label}")


def main() -> None:
    args = set(sys.argv[1:])

    if "--llm" in args:
        print(">>> Running REAL LLM tests ONLY")
        run(LOCAL_ONLY_TESTS, "real-llm")
        return

    print(">>> Running CI-SAFE tests")
    run(CI_TESTS, "ci-safe")

    if "--local" in args:
        print("\n>>> Running LOCAL-ONLY tests")
        run(LOCAL_ONLY_TESTS, "local-llm")

        print("\n>>> Running QUALITY tests (advisory)")
        run(QUALITY_TESTS, "quality")


if __name__ == "__main__":
    main()
