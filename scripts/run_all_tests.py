"""Repo-root wrapper for the LLM test runner.

Lives under scripts/ — resolves the repo root via parent.parent so it works
regardless of the working directory the user invokes it from.
"""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "llm" / "run_all_tests.py"


if __name__ == "__main__":
    raise SystemExit(
        subprocess.run([sys.executable, str(RUNNER), *sys.argv[1:]], check=False).returncode
    )
