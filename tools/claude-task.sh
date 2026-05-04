#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  CHESS COACH — CLAUDE TASK RUNNER"
echo "============================================"

# ── 1. PROJECT RULES ─────────────────────────────
echo ""
echo "[ 1/8 ] Reading project rules..."
if [ -f "CLAUDE.md" ]; then
  cat CLAUDE.md
else
  echo "WARNING: CLAUDE.md not found — create it with /init"
fi

# ── 2. ARCHITECTURE RULES ────────────────────────
echo ""
echo "[ 2/8 ] Reading architecture rules..."
if [ -f "docs/ARCHITECTURE.md" ]; then
  cat docs/ARCHITECTURE.md
else
  echo "WARNING: No architecture doc found at docs/ARCHITECTURE.md"
fi

# ── 3. THE TASK ──────────────────────────────────
echo ""
echo "[ 3/8 ] Task description:"
echo "${TASK:-${1:-'No task provided. Pass task as argument.'}}"

# ── 4. AFFECTED LAYER ────────────────────────────
echo ""
echo "[ 4/8 ] Project structure (identify affected layer):"
find . -type f \
  -not -path "./.git/*" \
  -not -path "./node_modules/*" \
  -not -path "./.claude/*" \
  | sort

# ── 5. MINIMAL CHANGES REMINDER ──────────────────
echo ""
echo "[ 5/8 ] RULE: Make minimal changes only."
echo "  - Touch only files required for this task"
echo "  - Do not refactor unrelated code"
echo "  - Do not change interfaces unless necessary"

# ── 6. OBJECTIVE TESTS ───────────────────────────
echo ""
echo "[ 6/8 ] Running tests..."
if [ -f "package.json" ] && grep -q '"test"' package.json; then
  npm test --if-present 2>&1 || echo "Tests failed — fix before committing"
elif [ -f "pytest.ini" ] || [ -f "pyproject.toml" ]; then
  python -m pytest 2>&1 || echo "Tests failed — fix before committing"
else
  echo "No test runner detected — add one to package.json or pytest.ini"
fi

# ── 7. VERIFY ARCHITECTURE ───────────────────────
echo ""
echo "[ 7/8 ] Architecture checklist:"
echo "  [ ] Changes stay within the correct layer"
echo "  [ ] No new dependencies added without justification"
echo "  [ ] No circular imports or coupling introduced"
echo "  [ ] Naming follows project conventions"

# ── 8. COMMIT MESSAGE ────────────────────────────
echo ""
echo "[ 8/8 ] Git status for commit message:"
git diff --stat 2>/dev/null || echo "Not a git repo or no changes"
echo ""
echo "Suggested commit format:"
echo "  <type>(<scope>): <what changed and why>"
echo "  types: feat | fix | refactor | test | docs | chore"
echo ""
echo "============================================"
echo "  TASK RUNNER COMPLETE"
echo "============================================"