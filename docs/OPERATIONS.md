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

Ollama installed and running

One supported LLM model available locally

Example:

qwen2.5:7b-instruct-q2_K

3.2 Required Environment Variables
LLM_MODEL=qwen2.5:7b-instruct-q2_K


If this variable is missing, the system must fail fast.

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

5.3 LLM Runtime Errors (Ollama)

Examples:

Unknown flags

Model not found

Encoding errors

Action

Treat as infrastructure error

Do not retry automatically

Restart Ollama if needed

5.4 Known Model-Controllability Limit (qwen2.5:7b-instruct-q2_K)

Status

Parked, scheduled for resolution before the next v*.*.* release tag.

Symptom

`test_llm_regression.py` (RUN_OLLAMA_TESTS=1) intermittently fails on
`validate_mode_2_negative` and `validate_mode_2_structure` patterns
even when the system prompt explicitly forbids the same words.
Observed during the 2026-04-28 pre-deploy verification: the model
produces "calculation" / "white can" / "line" tokens after being
shown the comprehensive forbidden vocabulary in ABSOLUTE RULE 9.

Root cause

Q2 quantization is the most aggressive variant available for
qwen2.5:7b-instruct (~3 GB on disk vs. ~4 GB for Q4_K_M).  The
quality-vs-memory trade-off bites hardest on negation constraints —
the model knows the words are forbidden but reaches for them anyway
when generating fluent prose.  This is documented behaviour for
heavily-quantized 7B-class models, not a bug in the prompt.

Production impact

Zero direct user-visible 500s.  The chat_pipeline retry loop
(MAX_MODE_2_RETRIES) catches violations and retries with a
strict-hint rewrite; if all retries fail, the deterministic
fallback in _build_reply_deterministic ships instead.  Users see
either a compliant LLM reply or the deterministic prose — never an
HTTP error.  The same retry+fallback chain is wired into
generate_chat_reply (chat_pipeline.py:528-571) and
generate_live_reply (live_move_pipeline.py:285-318).

What to do at the next release

Before the next `git tag v*.*.*` push (which gates the CI
`llm-regression` job), evaluate one or more of the following per
OPERATIONS.md §5.2's standard fix-list:

1.  Lower `LLM_TEMPERATURE` from 0.2 toward 0.0 (deterministic
    argmax).  Cheapest change, but neutralises REPEATS=3 in the
    regression test — output becomes deterministic.
2.  Switch model variant to `qwen2.5:7b-instruct-q4_K_M` (~+1 GB
    disk, materially better instruction-following).  Update both
    `COACH_OLLAMA_MODEL` in `.env.prod` and the hardcoded
    `MODEL_NAME` in `llm/rag/tests/llm/test_llm_regression.py`,
    `llm/rag/tests/llm/test_ollama_smoke.py`.  Pull the new model
    on the Hetzner Ollama volume before flipping the env var:
    `docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen2.5:7b-instruct-q4_K_M`
3.  Adjust RAG phrasing if specific forbidden tokens correlate with
    specific RAG documents.

Do NOT weaken the validators or the regression test to make this
green — per CLAUDE.md rule #5, the failure is the signal.

Telemetry to monitor before deciding

The retry-loop instrumentation in chat_pipeline (DEBUG-level logs
"Mode-2 LLM path failed" and "Chat LLM blocked by output firewall")
is the existing window into how often production hits this path.
Spike in either log line = controllability is degrading; flat or
declining = current model is acceptable in practice.

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

Require Ollama

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