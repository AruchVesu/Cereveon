OPERATIONS.md

ChessCoach-AI · Mode-2 Explanation System

1. Purpose of This Document

This document describes how to run, monitor, and maintain the ChessCoach-AI Mode-2 system in production.

It answers:

How the system is started

What must be monitored

What constitutes normal vs abnormal behavior

How regressions are detected

What to do when something breaks

This document does not describe:

Architecture (see ARCHITECTURE.md)

Testing philosophy (see TESTING.md)

Release process (see RELEASE.md)

2. Operational Scope

This document applies to:

Mode-2 explanation pipeline

Embedded deployment (rag.deploy.embedded)

Local / on-device / private backend usage

Source-available operation (see ../LICENSE.md)

It assumes:

No public API exposure

No multi-tenant environment

No user data persistence

3. System Startup
3.1 Environment Requirements

Required:

Python ≥ 3.11

DeepSeek API key (https://platform.deepseek.com)

Default model:

deepseek-chat (DeepSeek-V3, OpenAI-compatible /chat/completions)

3.2 Required Environment Variables
COACH_DEEPSEEK_API_KEY=sk-...      # DeepSeek API key, required for live LLM
COACH_DEEPSEEK_API_BASE=https://api.deepseek.com   # optional override
COACH_DEEPSEEK_MODEL=deepseek-chat  # optional override (default DeepSeek-V3)


If `COACH_DEEPSEEK_API_KEY` is missing, the system still serves but every
`/chat` call falls back to the deterministic template. `GET /llm/health`
surfaces the live LLM status so degraded mode is detectable in monitoring.

3.3 Starting the System (Embedded Mode)

From project root:

python host_app.py


Expected behavior:

No warnings

No silent failures

Either:

Valid explanation returned

Explicit exception raised

Silent output is not acceptable.

4. Runtime Execution Flow (Operational View)
Host App
  ↓
explain_position()
  ↓
extract_engine_signal()
  ↓
retrieve()          (RAG)
  ↓
render_mode_2_prompt()
  ↓
LLM.generate()
  ↓
validate_mode_2_negative()   ← hard gate
  ↓
score_explanation()          ← quality gate
  ↓
record_quality_score()      ← telemetry
  ↓
return explanation


Any failure stops execution immediately.

5. Failure Modes & Expected Responses
5.1 Validator Failure (Forbidden Patterns)

Example:

AssertionError: Mode-2 violation detected: pattern `checkmate`


Meaning

LLM violated a non-negotiable rule

Output was blocked correctly

Action

Do NOT weaken validator

Tighten system prompt if recurring

Inspect telemetry trend

5.2 Quality Score Failure

Example:

AssertionError: Explanation quality too low: 6/10


Meaning

Output was legal but insufficiently explanatory

Action

Acceptable in development

In production:

Either surface error

Or retry with same prompt (optional, bounded)

Do NOT auto-accept low-quality output.

5.3 LLM Runtime Errors (DeepSeek)

Examples:

HTTP 401 — invalid or revoked API key

HTTP 402 — billing exhausted / payment failed

HTTP 429 — rate limit exceeded

HTTP 5xx — DeepSeek upstream incident

Action

Treat as infrastructure error.  The chat_pipeline retry loop
(MAX_MODE_2_RETRIES) does not retry on these — instead the
exception is logged at WARNING level (chat_pipeline.py:557) and
the deterministic fallback in `_build_reply_deterministic` ships
to the user.  Users see template prose, never an HTTP error.

Confirm via `GET /llm/health` — the response carries the upstream
HTTP code and DeepSeek's error message in its `error` field, e.g.
`{"ok": false, "error": "HTTP 401: Authentication Fails, Your api key is invalid"}`.

For 401 / 402, rotate `COACH_DEEPSEEK_API_KEY` in `.env.prod` and
restart api: `docker compose -f docker-compose.prod.yml up -d --force-recreate api`.

For 429, check DeepSeek's [rate-limit dashboard](https://platform.deepseek.com).
The api falls back gracefully so users never see the limit; the
WARNING log lines are the operator signal.

5.4 Switching the LLM model (or provider)

Status

DeepSeek-V3 (`deepseek-chat`) is the production default.  Switching
to a different OpenAI-compatible model or provider is one env-var
change.

Procedure

1.  Update `COACH_DEEPSEEK_MODEL` in `.env.prod` to the new model
    string (e.g. `deepseek-reasoner` for chain-of-thought, or any
    OpenAI-compatible model name on a different gateway).
2.  If you're switching providers entirely (OpenAI, Together, Groq,
    self-hosted vLLM), also update `COACH_DEEPSEEK_API_BASE` to the
    new endpoint and `COACH_DEEPSEEK_API_KEY` to the new key.  The
    code talks pure OpenAI-compatible JSON so no Python changes
    are needed for any of these.
3.  `docker compose -f docker-compose.prod.yml up -d --force-recreate api`
4.  Probe `GET /llm/health` — should return `ok: true` within a
    few seconds.

Do NOT weaken the validators or the regression test to accommodate
a worse-behaved model — per CLAUDE.md rule #5, the failure is the
signal.

Telemetry to monitor

The retry-loop instrumentation in chat_pipeline (WARNING-level log
"Mode-2 LLM path failed" and DEBUG-level "Chat LLM blocked by
output firewall") is the operator window into how often production
hits the fallback path.  Spike in either log line = controllability
is degrading; flat or declining = current model is acceptable in
practice.  `docker compose -f docker-compose.prod.yml logs api -f | grep -i fallback`.

6. Telemetry Operations
6.1 What Is Collected

Stored in:

telemetry/quality_scores.jsonl


Each record contains:

timestamp

score (0–10)

case_type

model name

mode

No text, no prompts, no user data.

6.2 Normal Telemetry Profile

Healthy distribution:

Majority scores: 8–9

Some scores: 7

Rare scores: ≤6

Unhealthy indicators:

Mean score trending downward

Spike in scores exactly at 7

Repeated failures after model change

6.3 Telemetry Maintenance

File is append-only

Safe to delete between runs

Should be .gitignored

May be rotated manually

Telemetry must never influence runtime decisions.

7. Regression Detection

Regressions are detected via:

Negative golden tests

Positive golden tests

Prompt snapshot tests

Quality score thresholds

Telemetry trend analysis

If any regression test fails:

Stop deployment

Fix before release

8. CI Expectations (Operational)

CI must enforce:

All golden tests pass

Validators remain active

Prompt snapshots unchanged unless intentional

No test skips

CI must not:

Require a real LLM provider (DeepSeek API key, etc.)

Execute live LLM inference

Depend on telemetry

9. Model Upgrades (Operational Rules)

When changing LLM model:

Update LLM_MODEL

Run full golden test suite

Run local inference multiple times

Observe telemetry distribution

Only then promote model

Never upgrade models silently.

10. Incident Response Checklist

If something goes wrong:

Identify failure type

Confirm validator behavior

Check recent prompt changes

Inspect telemetry trends

Roll back last change if needed

Never bypass safety layers to “get output”.

11. Non-Goals (Explicit)

This system does NOT aim to:

Be creative

Provide best chess moves

Replace engine analysis

Optimize for verbosity

Personalize explanations

It aims to be:

Safe

Deterministic

Explainable

Regression-proof

12. Operational Invariant (Memorize This)

No output is always better than unsafe output.

If the system refuses to respond, that is a success condition, not a failure.

End of OPERATIONS.md