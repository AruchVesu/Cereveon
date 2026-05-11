"""Query the .mutmut-cache SQLite DB and print a baseline summary.

Replaces ``mutmut results`` which crashes on mutmut 2.5.x +
Python 3.13 with ``TypeError: 'QueryResultIterator' object is not
iterable`` (pony.orm compat bug).

Usage:
    python scripts/_mutation_baseline_query.py

Reads ``.mutmut-cache`` in the current working directory.  Prints
per-file killed/survived/total/kill-rate, then lists every surviving
mutant with its file:line and source snippet.

Run after ``bash scripts/run_mutation_tests.sh``.  The output is the
canonical input for refreshing ``scripts/mutation_baseline.txt``.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    cache = Path(".mutmut-cache")
    if not cache.exists():
        print("ERROR: .mutmut-cache not found in cwd.  Run mutmut first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(cache)

    # Per-file totals
    rows = conn.execute(
        """
        SELECT sf.filename, m.status, COUNT(*) AS n
        FROM Mutant m
        JOIN Line l ON m.line = l.id
        JOIN SourceFile sf ON l.sourcefile = sf.id
        GROUP BY sf.filename, m.status
        ORDER BY sf.filename, m.status
        """
    ).fetchall()

    totals: dict[str, dict[str, int]] = {}
    for filename, status, count in rows:
        slot = totals.setdefault(filename, {"killed": 0, "survived": 0, "other": 0})
        if status == "ok_killed":
            slot["killed"] += count
        elif status == "bad_survived":
            slot["survived"] += count
        else:
            slot["other"] += count

    print("=== Per-file mutation results ===")
    print(f"{'file':<55} {'killed':>7} {'survived':>9} {'total':>7} {'kill_rate':>10}")
    grand_killed = grand_survived = 0
    for filename, slot in sorted(totals.items()):
        total = slot["killed"] + slot["survived"]
        kill_rate = 100 * slot["killed"] // max(total, 1)
        print(
            f"{filename:<55} "
            f"{slot['killed']:>7} "
            f"{slot['survived']:>9} "
            f"{total:>7} "
            f"{kill_rate:>9}%"
        )
        grand_killed += slot["killed"]
        grand_survived += slot["survived"]
    grand_total = grand_killed + grand_survived
    grand_rate = 100 * grand_killed // max(grand_total, 1)
    print(f"{'TOTAL':<55} {grand_killed:>7} {grand_survived:>9} {grand_total:>7} {grand_rate:>9}%")

    # Survivor details
    survivors = conn.execute(
        """
        SELECT sf.filename, l.line_number, l.line, m.id
        FROM Mutant m
        JOIN Line l ON m.line = l.id
        JOIN SourceFile sf ON l.sourcefile = sf.id
        WHERE m.status = 'bad_survived'
        ORDER BY sf.filename, l.line_number, m.id
        """
    ).fetchall()

    if survivors:
        print()
        print(f"=== {len(survivors)} surviving mutants ===")
        print("(each is a missing test case; close by adding a test that")
        print(" fails when the validator at file:line is wrong)")
        print()
        for filename, lineno, source, mutant_id in survivors:
            snippet = (source or "").strip()
            if len(snippet) > 78:
                snippet = snippet[:75] + "..."
            print(f"  [{mutant_id:>4}] {filename}:{lineno}")
            print(f"         {snippet}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
