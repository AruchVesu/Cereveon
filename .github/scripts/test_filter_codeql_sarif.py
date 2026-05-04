"""
Regression tests for filter_codeql_sarif.py.

The previous version of this filter required a function-name substring
match in addition to ruleId+path.  CodeQL SARIF doesn't carry function
names in the result object, so the filter never matched and every
suppressed alert was uploaded every run.  These tests pin the realistic
SARIF shape so that bug cannot recur.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_FILTER = os.path.join(_THIS_DIR, "filter_codeql_sarif.py")


def _realistic_codeql_alert(rule_id: str, file_uri: str, line: int = 43) -> dict:
    """Build a SARIF result with the exact shape CodeQL emits for Python.

    Function names are deliberately absent — they live in the GitHub UI's
    derived view, not in the underlying SARIF payload."""
    return {
        "ruleId": rule_id,
        "ruleIndex": 0,
        "message": {"text": "This use of a hashing algorithm is insecure for sensitive data."},
        "locations": [{
            "physicalLocation": {
                "artifactLocation": {"uri": file_uri, "uriBaseId": "%SRCROOT%"},
                "region": {"startLine": line, "startColumn": 12, "endLine": line, "endColumn": 65},
            }
        }],
        "partialFingerprints": {"primaryLocationLineHash": "abc123def456"},
    }


def _wrap_run(results: list[dict]) -> dict:
    return {
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "CodeQL"}},
            "results": results,
        }],
    }


class TestSarifFilter(unittest.TestCase):
    def _run_filter(self, sarif: dict) -> tuple[dict, str, int]:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "python.sarif")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sarif, fh)
            r = subprocess.run(
                [sys.executable, _FILTER, tmp],
                capture_output=True, text=True,
            )
            with open(path, "r", encoding="utf-8") as fh:
                out = json.load(fh)
            return out, r.stdout, r.returncode

    def test_drops_accepted_false_positive_with_realistic_sarif(self):
        """Critical: CodeQL SARIF carries no function names.  The filter
        must match on (ruleId, file path) alone — older versions required
        a function-name substring and silently failed on every run."""
        sarif = _wrap_run([
            _realistic_codeql_alert(
                "py/weak-cryptographic-hash",
                "llm/seca/auth/hashing.py",
            ),
        ])
        out, stdout, rc = self._run_filter(sarif)
        self.assertEqual(rc, 0, f"filter exited non-zero: {stdout}")
        self.assertEqual(
            len(out["runs"][0]["results"]), 0,
            "Realistic CodeQL alert was NOT dropped — this is the exact bug "
            "that caused the GitHub Security tab alert to persist for "
            "multiple workflow runs.",
        )

    def test_keeps_same_rule_in_unrelated_file(self):
        """The suppression must NOT swallow the same ruleId hitting a
        different file — the query stays active for every other path."""
        sarif = _wrap_run([
            _realistic_codeql_alert(
                "py/weak-cryptographic-hash",
                "llm/some/other/module.py",
            ),
        ])
        out, _, rc = self._run_filter(sarif)
        self.assertEqual(rc, 0)
        self.assertEqual(
            len(out["runs"][0]["results"]), 1,
            "Filter incorrectly dropped a same-rule alert in an unrelated file.",
        )

    def test_keeps_different_rule_in_same_file(self):
        """A different rule landing in the same file must still be reported."""
        sarif = _wrap_run([
            _realistic_codeql_alert(
                "py/sql-injection",
                "llm/seca/auth/hashing.py",
            ),
        ])
        out, _, rc = self._run_filter(sarif)
        self.assertEqual(rc, 0)
        self.assertEqual(
            len(out["runs"][0]["results"]), 1,
            "Filter swallowed an unrelated rule in the suppressed file.",
        )

    def test_mixed_alerts_drops_only_the_accepted_one(self):
        sarif = _wrap_run([
            _realistic_codeql_alert(
                "py/weak-cryptographic-hash",
                "llm/seca/auth/hashing.py",
            ),
            _realistic_codeql_alert(
                "py/weak-cryptographic-hash",
                "llm/somewhere/else.py",
            ),
            _realistic_codeql_alert(
                "py/sql-injection",
                "llm/seca/auth/hashing.py",
            ),
        ])
        out, _, rc = self._run_filter(sarif)
        self.assertEqual(rc, 0)
        results = out["runs"][0]["results"]
        self.assertEqual(len(results), 2,
                         "Expected exactly the suppressed alert dropped, "
                         "two unrelated alerts preserved.")
        rules_kept = sorted(r["ruleId"] for r in results)
        self.assertEqual(rules_kept, ["py/sql-injection", "py/weak-cryptographic-hash"])
        # The kept weak-crypto-hash must be the one in 'somewhere/else.py'
        kept_paths = [
            r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
            for r in results
        ]
        self.assertNotIn(
            "llm/seca/auth/hashing.py",
            [p for p, r in zip(kept_paths, results) if r["ruleId"] == "py/weak-cryptographic-hash"],
            "The hashing.py weak-crypto-hash alert was kept — filter is broken.",
        )

    def test_drops_renamed_query_id(self):
        """A future CodeQL release could rename
        py/weak-cryptographic-hash to py/weak-sensitive-data-hashing or
        the older py/insecure-cryptographic-hash without changing the
        underlying check.  The filter must suppress all known IDs for
        the same vulnerability class on the same file — otherwise a
        rename silently reopens alert #3."""
        for rule_id in (
            "py/weak-sensitive-data-hashing",
            "py/insecure-cryptographic-hash",
        ):
            with self.subTest(rule_id=rule_id):
                sarif = _wrap_run([
                    _realistic_codeql_alert(rule_id, "llm/seca/auth/hashing.py"),
                ])
                out, _, rc = self._run_filter(sarif)
                self.assertEqual(rc, 0)
                self.assertEqual(
                    len(out["runs"][0]["results"]), 0,
                    f"Filter did not drop renamed rule id {rule_id}",
                )

    def test_drops_when_ruleid_only_in_rule_object(self):
        """SARIF allows result.rule.id instead of result.ruleId.  A
        previous filter version checked only the flat field and missed
        alerts emitted under the embedded shape — that is the exact
        mode in which alert #3 has reopened in past scans."""
        sarif = _wrap_run([{
            # Note: NO 'ruleId' top-level field.
            "rule": {"id": "py/weak-cryptographic-hash", "index": 0},
            "message": {"text": "Weak hash."},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": "llm/seca/auth/hashing.py", "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": 43, "startColumn": 12},
                }
            }],
        }])
        out, _, rc = self._run_filter(sarif)
        self.assertEqual(rc, 0)
        self.assertEqual(
            len(out["runs"][0]["results"]), 0,
            "Filter ignored embedded rule.id — alert leaks through.",
        )

    def test_drops_when_ruleid_only_via_index(self):
        """SARIF can carry just ruleIndex pointing into
        runs[].tool.driver.rules[].  CodeQL's modern emitter sometimes
        uses this shape; the filter must resolve through the driver's
        rules array."""
        sarif = {
            "version": "2.1.0",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "CodeQL",
                        "rules": [
                            {"id": "py/weak-cryptographic-hash"},
                            {"id": "py/sql-injection"},
                        ],
                    },
                },
                "results": [{
                    # Only ruleIndex; no ruleId, no rule.id.
                    "ruleIndex": 0,
                    "message": {"text": "Weak hash."},
                    "locations": [{
                        "physicalLocation": {
                            "artifactLocation": {"uri": "llm/seca/auth/hashing.py", "uriBaseId": "%SRCROOT%"},
                            "region": {"startLine": 43, "startColumn": 12},
                        }
                    }],
                }],
            }],
        }
        out, _, rc = self._run_filter(sarif)
        self.assertEqual(rc, 0)
        self.assertEqual(
            len(out["runs"][0]["results"]), 0,
            "Filter ignored ruleIndex — alert leaks through when CodeQL "
            "emits results referencing the driver rules array.",
        )

    def test_handles_malformed_rule_index_gracefully(self):
        """A bogus ruleIndex (out of range, missing rules array,
        non-int) must not crash the filter — just fall back to the
        other lookups and process normally."""
        sarif = {
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {"name": "CodeQL"}},  # no rules array
                "results": [{
                    "ruleId": "py/weak-cryptographic-hash",
                    "ruleIndex": 99,  # out-of-range; should be ignored
                    "message": {"text": "Weak hash."},
                    "locations": [{
                        "physicalLocation": {
                            "artifactLocation": {"uri": "llm/seca/auth/hashing.py", "uriBaseId": "%SRCROOT%"},
                            "region": {"startLine": 43},
                        }
                    }],
                }],
            }],
        }
        out, _, rc = self._run_filter(sarif)
        self.assertEqual(rc, 0)
        self.assertEqual(
            len(out["runs"][0]["results"]), 0,
            "Malformed ruleIndex broke fallback to flat ruleId lookup.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
