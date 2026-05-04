"""Run the comprehensive stress test suite across all 10 system areas.

Each area is run as a separate pytest invocation or Gradle command so that
failures are isolated and clearly attributed.  The script exits non-zero as
soon as the first area fails, matching the fail-fast behaviour of the CI
regression pipeline.

Usage::

    python llm/run_stress_suite.py              # all 10 areas
    python llm/run_stress_suite.py --skip-android  # skip Android (areas 7-8)

Areas (in execution order):
  1. Engine evaluation cache         — concurrency, LRU pressure, key integrity
  2. LLM schema validation           — adversarial payloads, fuzz, injection
  3. API contract tests              — schema stability, concurrent calls
  4. Game analysis pipeline          — large PGN, 1000-event runs, corrupted data
  5. Player analytics engine         — volume aggregation, float precision
  6. Training recommendation engine  — threshold sweep, priority stability
  7. Android Quick Coach UI          — ViewModel turn machine, cancellation
  8. Android Chat Coach              — ChatMessage / data model stress
  9. Engine performance benchmarks   — extended corpus, sustained SLO
 10. CI/CD regression pipeline       — structural integrity, file existence
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRESS_TEST_FILE = "llm/tests/test_stress_suite.py"

# ---------------------------------------------------------------------------
# Python stress groups (areas 1–6, 9–10)
# ---------------------------------------------------------------------------

PYTHON_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Area 1 — Engine evaluation cache",
        [f"{STRESS_TEST_FILE}::TestCacheStressConcurrency"],
    ),
    (
        "Area 2 — LLM schema validation",
        [f"{STRESS_TEST_FILE}::TestSchemaValidationStress"],
    ),
    (
        "Area 3 — API contract tests",
        [f"{STRESS_TEST_FILE}::TestApiContractStress"],
    ),
    (
        "Area 4 — Game analysis pipeline",
        [f"{STRESS_TEST_FILE}::TestGameAnalysisPipelineStress"],
    ),
    (
        "Area 5 — Player analytics engine",
        [f"{STRESS_TEST_FILE}::TestPlayerAnalyticsStress"],
    ),
    (
        "Area 6 — Training recommendation engine",
        [f"{STRESS_TEST_FILE}::TestTrainingRecommendationStress"],
    ),
    (
        "Area 9 — Engine performance benchmarks (extended)",
        [f"{STRESS_TEST_FILE}::TestExtendedBenchmarkCorpus"],
    ),
    (
        "Area 10 — CI/CD regression pipeline",
        [f"{STRESS_TEST_FILE}::TestCiCdPipelineStress"],
    ),
]

# ---------------------------------------------------------------------------
# Android stress groups (areas 7–8) — run via Gradle
# ---------------------------------------------------------------------------

ANDROID_GROUPS: list[tuple[str, str]] = [
    (
        "Area 7 — Android Quick Coach UI (QuickCoachStressTest)",
        "QuickCoachStressTest",
    ),
    (
        "Area 8 — Android Chat Coach (ChatCoachStressTest)",
        "ChatCoachStressTest",
    ),
]


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def _run_python_group(label: str, targets: list[str]) -> int:
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  STRESS GROUP: {label}")
    print(sep)
    cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short", *targets]
    return subprocess.run(cmd, cwd=PROJECT_ROOT, check=False).returncode


def _run_android_group(label: str, test_class: str) -> int:
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  STRESS GROUP: {label}")
    print(sep)

    android_dir = PROJECT_ROOT / "android"
    if not android_dir.exists():
        print(f"  [SKIP] Android directory not found: {android_dir}")
        return 0

    # Choose the correct Gradle wrapper name for the OS
    if platform.system() == "Windows":
        gradlew = str(android_dir / "gradlew.bat")
    else:
        gradlew = "./gradlew"

    cmd = [
        gradlew,
        "testDebugUnitTest",
        f"--tests=ai.chesscoach.app.{test_class}",
    ]
    result = subprocess.run(cmd, cwd=android_dir, check=False)
    if result.returncode != 0:
        print(f"\n  [WARN] Android group '{label}' failed or Gradle unavailable.")
        print("         Run manually: cd android && ./gradlew test")
    return result.returncode


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(*, skip_android: bool = False) -> int:
    print("=" * 60)
    print("  CHESSCOACH STRESS TEST SUITE")
    print(f"  Total Python groups : {len(PYTHON_GROUPS)}")
    print(f"  Total Android groups: {len(ANDROID_GROUPS)}")
    print(f"  Skip Android        : {skip_android}")
    print("=" * 60)

    failed: list[str] = []

    # Python areas (1–6, 9–10)
    for label, targets in PYTHON_GROUPS:
        code = _run_python_group(label, targets)
        if code != 0:
            failed.append(label)
            print(f"\n[FAIL] {label}")
            break

    # Android areas (7–8)
    if not failed and not skip_android:
        for label, test_class in ANDROID_GROUPS:
            code = _run_android_group(label, test_class)
            if code != 0:
                failed.append(label)
                print(f"\n[FAIL] {label}")
                break

    # Summary
    print("\n" + "=" * 60)
    if failed:
        print(f"STRESS SUITE FAILED — first broken area: {failed[0]}")
        return 1

    total = len(PYTHON_GROUPS) + (0 if skip_android else len(ANDROID_GROUPS))
    print(f"STRESS SUITE PASSED — {total} areas OK")
    return 0


if __name__ == "__main__":
    skip = "--skip-android" in sys.argv
    raise SystemExit(main(skip_android=skip))
