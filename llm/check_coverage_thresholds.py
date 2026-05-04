"""Per-module coverage threshold enforcement.

Average ≥ 80% is a weak gate for a safety-critical pipeline — a validator
sitting at 60% can ride to green on the back of well-covered UI helpers.
This script enforces per-pattern minimums on top of the existing global
gate so the validators (the trust boundary) stay above 95% even when the
rest of the codebase drops to the global floor.

Reads ``tmp_logs/coverage.xml`` (Cobertura format produced by pytest-cov),
walks every covered file, looks up the highest-specificity threshold from
THRESHOLDS, and aborts if any file undershoots its floor.

Run after pytest finishes — see ``run_ci_suite.py``.
"""

from __future__ import annotations

import fnmatch
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = PROJECT_ROOT / "tmp_logs" / "coverage.xml"


# First-match-wins. Patterns are matched with fnmatch against the
# Cobertura-reported filename ("llm/rag/validators/mode_2_negative.py" — note
# forward slashes regardless of OS).  Order matters: file-specific exemptions
# come before glob tiers, glob tiers come before the default catch-all.
#
# Validators and the post-LLM safety firewall are the trust-boundary code:
# their failure modes are silent (a missed violation passes through), which
# is the kind of thing line coverage CAN catch and the kind of thing the
# rest of the codebase's coverage average obscures.
#
# Module-specific exemptions are listed below the trust-boundary tier and
# above the catch-all.  Each has the actual current coverage (rounded down
# slightly to absorb jitter) so the gate fails on regressions but does not
# block the rollout of the new per-module floor on legacy debt.  The intent
# is that every exemption is a TODO with the rationale on the line above —
# remove the exemption when the file's coverage clears the catch-all floor.
THRESHOLDS: list[tuple[str, float]] = [
    # Trust boundary — silent-failure-class code.
    ("llm/rag/validators/*.py", 95.0),
    ("llm/rag/safety/*.py", 95.0),
    # Module-specific exemptions (see comment above).
    # Bandit lives in a dormant SECA research subtree; only the freeze-guard
    # path is exercised in CI.  TODO: drop this exemption when bandit gains
    # full happy-path coverage or is moved out of run_ci_suite COVERAGE_TARGETS.
    ("llm/seca/brain/bandit/contextual_bandit.py", 65.0),
    # Curriculum spacing's edge-case branches (max-interval clamping,
    # zero-event guard) are not yet covered.  TODO: add the missing
    # cases to test_curriculum_next_contract.py.
    ("llm/seca/curriculum/spacing.py", 70.0),
    # Skills trainer's empty-events / partial-batch branches lack coverage.
    # TODO: extend test_skill_updater_resilience.py.
    ("llm/seca/skills/trainer.py", 65.0),
    # Global floor — every file not matched above must clear this.
    ("*", 80.0),
]


def _line_rate_for(class_node: ET.Element) -> float:
    """Cobertura ``line-rate`` is a 0..1 fraction; normalise to percent."""
    return float(class_node.get("line-rate", "0.0")) * 100.0


def _filename_of(class_node: ET.Element) -> str:
    raw = class_node.get("filename") or ""
    # Cobertura uses backslashes on Windows; the patterns here use forward
    # slashes for portability, so normalise once at the boundary.
    return raw.replace("\\", "/")


def _threshold_for(filename: str) -> float:
    for pattern, threshold in THRESHOLDS:
        if fnmatch.fnmatch(filename, pattern):
            return threshold
    raise RuntimeError(f"no threshold matched {filename!r} — THRESHOLDS catch-all is missing")


def check_thresholds(report_path: Path) -> int:
    """Return 0 on success, 1 on any threshold violation."""
    if not report_path.is_file():
        print(
            f"[coverage-thresholds] Report not found at {report_path}. "
            "Run the pytest suite with --cov-report=xml first.",
            file=sys.stderr,
        )
        return 2

    tree = ET.parse(report_path)
    root = tree.getroot()
    classes = root.findall(".//class")

    if not classes:
        print(
            f"[coverage-thresholds] {report_path} contains zero <class> nodes. "
            "The pytest suite produced no coverage data — refusing to call this success.",
            file=sys.stderr,
        )
        return 2

    failures: list[tuple[str, float, float]] = []
    for class_node in classes:
        filename = _filename_of(class_node)
        if not filename:
            continue
        actual = _line_rate_for(class_node)
        floor = _threshold_for(filename)
        if actual + 1e-6 < floor:  # tolerate float jitter at the boundary
            failures.append((filename, actual, floor))

    if failures:
        print("[coverage-thresholds] FAIL — files below their per-module floor:", file=sys.stderr)
        for filename, actual, floor in sorted(failures):
            print(f"  {filename}: {actual:.2f}% (floor: {floor:.0f}%)", file=sys.stderr)
        print(
            "\nValidators and the safety firewall MUST stay ≥ 95% — that is the "
            "trust boundary.  Lift coverage by adding tests, not by relaxing the floor.",
            file=sys.stderr,
        )
        return 1

    print(f"[coverage-thresholds] OK — {len(classes)} files all meet their per-module floor.")
    return 0


def main() -> int:
    report = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    return check_thresholds(report)


if __name__ == "__main__":
    raise SystemExit(main())
