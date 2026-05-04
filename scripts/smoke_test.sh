#!/usr/bin/env bash
# Post-deploy smoke test for the Chess Coach backend.
#
# Usage:
#   ./scripts/smoke_test.sh [BASE_URL] [API_KEY]
#
# BASE_URL  defaults to http://localhost:8000
# API_KEY   defaults to $SECA_API_KEY env var (may be empty in dev mode)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#
# Requires: curl, python3 (for JSON parsing)

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
API_KEY="${2:-${SECA_API_KEY:-}}"

PASS=0
FAIL=0

_green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
_red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
_bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

check() {
    local name="$1" result="$2" expect="$3"
    if [ "$result" = "$expect" ]; then
        _green "  PASS  $name"
        PASS=$((PASS + 1))
    else
        _red "  FAIL  $name  (got: $result, want: $expect)"
        FAIL=$((FAIL + 1))
    fi
}

_bold "Smoke-testing ${BASE_URL}"
echo ""

# -----------------------------------------------------------------------
# 1. GET /health  — no auth, must return {"status":"ok"}
# -----------------------------------------------------------------------
_bold "1. GET /health"
resp=$(curl -sf --max-time 10 "${BASE_URL}/health" 2>&1) || {
    _red "  FAIL  /health: curl error — is the server running?"
    FAIL=$((FAIL + 1))
    resp=""
}
if [ -n "$resp" ]; then
    status=$(python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('status',''))" <<< "$resp" 2>/dev/null || echo "")
    check "status == ok" "$status" "ok"
fi
echo ""

# -----------------------------------------------------------------------
# 2. GET /debug/engine  — requires X-Api-Key, pool_size must be > 0
# -----------------------------------------------------------------------
_bold "2. GET /debug/engine"
if [ -z "$API_KEY" ]; then
    _red "  SKIP  API_KEY not set — cannot test /debug/engine (set SECA_API_KEY or pass as arg 2)"
    FAIL=$((FAIL + 1))
else
    resp=$(curl -sf --max-time 10 \
        -H "X-Api-Key: ${API_KEY}" \
        "${BASE_URL}/debug/engine" 2>&1) || {
        _red "  FAIL  /debug/engine: curl error"
        FAIL=$((FAIL + 1))
        resp=""
    }
    if [ -n "$resp" ]; then
        pool_size=$(python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('pool_size',-1))" <<< "$resp" 2>/dev/null || echo "-1")
        if python3 -c "import sys; sys.exit(0 if int('${pool_size}') > 0 else 1)" 2>/dev/null; then
            _green "  PASS  pool_size > 0  (pool_size=${pool_size})"
            PASS=$((PASS + 1))
        else
            _red "  FAIL  pool_size not > 0  (pool_size=${pool_size}) — is Stockfish installed?"
            FAIL=$((FAIL + 1))
        fi
    fi
fi
echo ""

# -----------------------------------------------------------------------
# 3. POST /engine/eval  — starting FEN, best_move must be non-null
# -----------------------------------------------------------------------
_bold "3. POST /engine/eval"
START_FEN="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
resp=$(curl -sf --max-time 30 \
    -H "Content-Type: application/json" \
    -d "{\"fen\":\"${START_FEN}\",\"movetime_ms\":200}" \
    "${BASE_URL}/engine/eval" 2>&1) || {
    _red "  FAIL  /engine/eval: curl error"
    FAIL=$((FAIL + 1))
    resp=""
}
if [ -n "$resp" ]; then
    best_move=$(python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('best_move') or '')" <<< "$resp" 2>/dev/null || echo "")
    if [ -n "$best_move" ]; then
        _green "  PASS  best_move=${best_move}"
        PASS=$((PASS + 1))
    else
        _red "  FAIL  best_move is null or missing — engine may be unavailable"
        FAIL=$((FAIL + 1))
    fi
fi
echo ""

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
_bold "Results: ${PASS} passed, ${FAIL} failed"
echo ""
if [ "$FAIL" -gt 0 ]; then
    _red "Smoke test FAILED — do not route traffic to this instance."
    exit 1
else
    _green "Smoke test PASSED."
    exit 0
fi
