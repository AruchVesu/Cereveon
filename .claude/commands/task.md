---
allowed-tools: Bash(./tools/claude-task.sh:*), Read, Edit, Bash(git diff:*), Bash(git status:*), Bash(npm test:*)
description: Run a task using project rules and architecture constraints
---

## Task Context
!`./tools/claude-task.sh "$ARGUMENTS"`

## Your Instructions
You have read the project rules, architecture, and current state above.

Now execute this task: **$ARGUMENTS**

Follow all 8 steps strictly. Do not skip verification.
```

---

Then in Claude Code you simply run:
```
/task add move validation to the bishop piece