#!/usr/bin/env bash
set -euo pipefail

INPUT="$(cat)"

COMMAND="$(
  printf '%s' "$INPUT" | python -c "import json,sys; data=json.load(sys.stdin); print((data.get('tool_input') or {}).get('command', ''), end='')"
)"

deny() {
  local reason="$1"
  python - "$reason" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": sys.argv[1],
            }
        }
    )
)
PY
}

if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])rm -rf([[:space:]]|$)'; then
  deny "Blocked destructive command: rm -rf"
  exit 0
fi


if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])git reset --hard([[:space:]]|$)'; then
  deny "Blocked destructive command: git reset --hard"
  exit 0
fi

if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])git clean -fd([[:space:]]|$)'; then
  deny "Blocked destructive command: git clean -fd"
  exit 0
fi

if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])git checkout --([[:space:]]|$)'; then
  deny "Blocked destructive command: git checkout --"
  exit 0
fi

if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])git restore --source([[:space:]]|$)'; then
  deny "Blocked destructive command: git restore --source"
  exit 0
fi

SNAPSHOT_PATHS="$(
  git status --short --untracked-files=all 2>/dev/null |
    grep -E '^[ MADRCU?!]{2}[[:space:]]+.*(\.db|dump\.rdb)$' || true
)"

if printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:]])git (add|commit|push)([[:space:]]|$)' &&
   [ -n "$SNAPSHOT_PATHS" ]; then
  deny "Blocked git operation while .db or dump.rdb files are present; ignore or remove snapshots first"
  exit 0
fi

if printf '%s' "$COMMAND" | grep -Eq '(\.env($|[^[:alnum:]_])|[/\\]secrets?[/\\])'; then
  deny "Blocked access to secret-bearing files"
  exit 0
fi

exit 0
