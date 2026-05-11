#!/usr/bin/env bash
# Mutation testing for the validators (Category F — local, on-demand).
#
# The Mode-2 contract validators ARE the trust boundary.  Line coverage
# answers "did the test execute the validator?" — mutation testing answers
# the harder question: "would the test fail if the validator were wrong?"
# A test that imports a validator and asserts no exception is raised counts
# as 100% line coverage but catches no logic mutations; mutmut surfaces
# exactly that gap by mutating each operator / constant in turn and
# confirming a test fails for each one.
#
# Out-of-CI by design: mutmut runs are minutes-to-tens-of-minutes per
# validator, and the signal is advisory (a surviving mutant indicates a
# missing test, not necessarily a broken one).  Run locally before any
# substantive change to a validator, before any release that touches
# llm/rag/validators/, and at least quarterly as part of architectural
# review.
#
# Usage:
#   bash scripts/run_mutation_tests.sh                  # run against all validators
#   bash scripts/run_mutation_tests.sh mode_2_negative  # single module
#
# Requirements:
#   pip install mutmut
#
# Output:
#   * Live progress on stdout.
#   * Surviving mutants printed at the end with their diff against the
#     original.  Each surviving mutant is a TODO: a test that should
#     fail when the validator is mutated but did not.

set -euo pipefail

if ! command -v mutmut >/dev/null 2>&1; then
    echo "mutmut is not installed.  Run: pip install mutmut" >&2
    exit 2
fi

# Move to repo root so paths below are stable regardless of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Test runner used for each mutation. Kept narrow so the run is feasible:
# only validator-direct tests participate.  Adding more tests increases
# detection rate at linear cost in wall-clock time.
#
# test_api_contract_validation.py is included because Sprint 5.A wired the
# Mode-2 structure + semantic validators into the live boundary on
# /chat and /live/move responses (validate_chat_response and
# validate_live_move_response in explain_response_schema.py); the
# boundary tests are what kill mutants in those new delegation branches.
RUNNER="python -m pytest -q --no-header -x \
  llm/rag/tests/contracts/test_fake_llm.py \
  llm/rag/tests/contracts/test_violations_corpus.py \
  llm/rag/tests/contracts/test_mode_2_output.py \
  llm/rag/tests/test_output_firewall.py \
  llm/tests/test_api_contract_validation.py"

# Validators in scope. Order matches docs/TESTING.md Validator Coverage Matrix.
#
# explain_response_schema.py was added in the Sprint 6.D refresh
# (2026-05-12): its validate_chat_response / validate_live_move_response
# functions are the boundary that gates every coaching response on the
# Mode-2 structure + semantic checks, and Sprint 5.A added significant
# new logic to that file (the structure + semantic delegation calls).
# Treating it as a validator-grade trust boundary, not a routing helper.
ALL_TARGETS=(
    "llm/rag/validators/mode_2_negative.py"
    "llm/rag/validators/mode_2_structure.py"
    "llm/rag/validators/mode_2_semantic.py"
    "llm/rag/validators/sanitize.py"
    "llm/rag/validators/explain_response_schema.py"
    "llm/rag/contracts/validate_output.py"
    "llm/rag/safety/output_firewall.py"
)

# Single-module override: bash scripts/run_mutation_tests.sh mode_2_negative
if [[ $# -gt 0 ]]; then
    requested="$1"
    TARGETS=()
    for t in "${ALL_TARGETS[@]}"; do
        case "$t" in
            *"$requested"*) TARGETS+=("$t") ;;
        esac
    done
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
        echo "No target matched '$requested'.  Available targets:" >&2
        printf '  %s\n' "${ALL_TARGETS[@]}" >&2
        exit 2
    fi
else
    TARGETS=("${ALL_TARGETS[@]}")
fi

# mutmut writes its results database to .mutmut-cache by default; keep that
# under tmp_logs/ so it lands in the existing CI artifact directory and is
# already gitignored via tmp_logs/.
export MUTMUT_RESULTS_DIR="${REPO_ROOT}/tmp_logs/mutmut"
mkdir -p "$MUTMUT_RESULTS_DIR"

echo "=== MUTATION TESTING ==="
echo "Targets: ${TARGETS[*]}"
echo "Results: $MUTMUT_RESULTS_DIR"
echo

# Run mutmut for each target separately so a slow validator can be stopped
# without losing progress on the faster ones.
exit_code=0
for target in "${TARGETS[@]}"; do
    echo "--- $target ---"
    if ! mutmut run \
            --paths-to-mutate "$target" \
            --runner "$RUNNER" \
            --no-progress; then
        exit_code=$?
        echo "  mutmut reported survivors for $target (exit $exit_code)"
    fi
done

echo
echo "=== SURVIVING MUTANTS (TODOs) ==="
mutmut results || true

if [[ $exit_code -ne 0 ]]; then
    echo
    echo "One or more validators have surviving mutants — those are tests"
    echo "that did NOT fail when the validator's logic was changed.  Each"
    echo "surviving mutant is a missing test case.  Resolve before merging"
    echo "any change to the validator surface."
fi

exit "$exit_code"
