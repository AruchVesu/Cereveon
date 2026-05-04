# Contributing to Cereveon

This is a solo project today; this document exists because the architectural
constraints below are the most valuable design artifact in the repo and must
gate every PR — including PRs from a future-you who has forgotten the
context. Read this first. The rest of the file is routine.

## Forbidden changes (read this section first, every time)

The following are explicitly **forbidden** and will block any PR:

- **Weakening output validators.** Validators are the trust boundary; lifting
  a Mode-2 contract or relaxing the output firewall is a contract change, not
  a bug fix. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the
  Validator Coverage Matrix in [`docs/TESTING.md`](docs/TESTING.md).
- **Bypassing or replacing the ESV.** The Engine Signal Vector is the
  *only* engine-derived input permitted downstream of Stockfish — no raw
  evaluations, no principal variations, no depth/nodes/scores. Any new
  consumer must accept the ESV, not the raw `chess.engine` output.
- **Dynamic prompt mutation at runtime.** Mode-2 prompt rendering follows a
  fixed injection order, snapshot-tested via `test_prompt_snapshot.py`. Adding
  conditional template branches at runtime is forbidden; add a new prompt
  variant under `llm/rag/prompts/` if the template needs to vary.
- **LLM reasoning beyond provided inputs.** The LLM is a language realiser
  only — it may rephrase the ESV + RAG context, never reason about positions
  it has not been shown. The Mode-2 contracts encode this rule; the
  validators enforce it.
- **Autonomous RL implementation.** The SECA freeze guard
  (`llm/seca/safety/freeze.py`) blocks online training, bandit updates, and
  world-model learning at startup. Code that turns those back on is forbidden;
  the dormant research subtrees stay dormant by policy.
- **Disabling or skipping SECA enforcement.** The freeze guard runs at
  module import. Re-ordering import to defer it, or wrapping it in a
  conditional, is forbidden.
- **Weakening tests or validators to make them pass.** If a test is failing,
  the right move is to find the bug or update the contract deliberately —
  *never* to soften the assertion. The single-line "fix the assertion" PR is
  the most common shape of a regression on this codebase; it will be
  rejected.

If a change you want to make falls into one of the above, it is a *contract
change*, not a code change — open a discussion before writing code, and
update the documentation, the tests, and the validators in the same PR.

## Required reviews

PRs that touch the trust boundary or cross layers should be reviewed in
sequence by:

1. **`engine-specialist`** — for engine pool, UCI, JNI bridge, move
   normalisation, evaluation semantics.
2. **`backend-coach-specialist`** — for API routes, coaching pipeline, auth,
   RAG assembly, backend integration behaviour.
3. **`android-specialist`** — for Android UI, API client integration,
   Gradle-backed validation.
4. **`test-writer`** — for unit, integration, regression, and contract
   coverage of new surfaces.
5. **`devils-advocate`** — for hostile-input, security, memory,
   coroutine/lifecycle, and cross-language boundary audits, especially when
   the change is externally exposed.
6. **`architecture-reviewer`** — read-only compliance review before closing
   substantial work. This is the last gate.

## Required workflow

For every change:

1. Read [`CLAUDE.md`](CLAUDE.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
   and [`docs/TESTING.md`](docs/TESTING.md) before editing — these are the
   only places where the invariants live.
2. Use read-only inspection (`grep`, `find`, IDE search) first to find the
   relevant code paths. Don't edit a file you have not read.
3. Summarise the affected modules and propose a minimal plan in the PR
   description before pushing implementation commits.
4. Modify only the necessary files in the correct layer. Cross-layer changes
   need cross-layer review (see *Required reviews* above).
5. Run the relevant validation locally (see *Required checks* below) and fix
   failures as part of the same PR — never ship with a known-broken gate.
6. Finish with the required checks for the layers you touched and surface
   them in the PR description.

## Required checks (must be green before merge)

Run these locally before opening a PR. CI will run them again on push.

```bash
# Full CI suite — pytest + per-module coverage gate
python llm/run_ci_suite.py

# Quality gates — Black, Pylint, Mypy
python llm/run_quality_gate.py

# Targeted suites for the layers you touched
python -m pytest -q llm/rag/tests/golden/test_retriever.py
python -m pytest -q llm/rag/tests/golden/test_prompt_snapshot.py
python -m pytest -q llm/rag/tests/contracts/test_fake_llm.py
python -m pytest -q llm/rag/tests/contracts/test_violations_corpus.py
python -m pytest -q llm/tests/test_api_contract_validation.py
python -m pytest -q llm/tests/test_coaching_pipeline_regression.py

# Android (if you touched android/)
cd android && ./gradlew test

# Validator changes — additionally run mutation tests (slow; local only)
bash scripts/run_mutation_tests.sh
```

If you touched a validator, the corpus, or the freeze guard, the rule of
thumb is: the [Validator Coverage Matrix](docs/TESTING.md) row for the rule
you changed must be updated in the same commit, and the matching corpus
entry in `llm/rag/tests/contracts/fixtures/violations.jsonl` must still be
rejected end-to-end.

## Repo structure

- `llm/` — backend coaching API, RAG pipeline, auth, SECA flows, backend tests
- `android/` — Android client (Atrium UI) + Gradle-backed validation surface
- `engine/` — native C++ opponent engine + JNI bridge
- `docs/` — formal architecture, testing, threat model, deployment, release
- `design/` — React/Babel design canvas (visual prototype, not in build)
- `scripts/` — operator-facing scripts (`hetzner_setup.sh`, `smoke_test.sh`,
  `run_mutation_tests.sh`, `run_connected_android_tests.sh`)
- `.claude/` — project subagents and deterministic governance hooks

## Setup

See [`README.md`](README.md) > *Developer Setup* for the full setup matrix
(Docker, Dev Container, bare-metal Python, Android). Don't duplicate it
here — that section is the source of truth.

## Commit messages

Style: descriptive, imperative-mood subject (not past tense), body that
explains *why* not just *what*. Match the existing recent log:

```
git log --oneline -10
```

For commits that touch the trust boundary, include in the body:

- Which validator / contract changed
- Which corpus entry / golden case was updated
- Which downstream consumers were verified

Per [`CLAUDE.md`](CLAUDE.md) rule 9: "Commits must describe changes in
detail." Treat the commit message as part of the design record, not as
release-note copy.

## Pull requests

PR title: same standard as commit subject — short, imperative, ≤ 70 chars.

PR body should include:

1. **Summary** — 1–3 bullets, what shipped.
2. **Architectural impact** — explicitly call out which invariant the
   change touches (or "none" if it's a leaf-level fix). If the answer is
   "none," double-check by re-reading the *Forbidden changes* section above.
3. **Test plan** — bulleted checklist matching the *Required checks*
   commands you actually ran, with results.
4. **Open questions** — anything you deferred, anything a reviewer should
   probe.

## Reporting issues

Bugs in the system: open a GitHub issue with reproduction steps + the
relevant log lines, scoped narrowly. Issues that touch the trust boundary
(LLM bypassing a validator, ESV being mis-extracted, freeze guard failing
to fire) should carry the `safety` label and call out the affected layer.

Security findings: do **not** open a public issue. See
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) for the model and contact
the project owner directly via the email in [`LICENSE.md`](LICENSE.md).

## Further reading

- [`CLAUDE.md`](CLAUDE.md) — agent rules, subagent routing, hook policy
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — invariants, trust
  boundaries, data flow
- [`docs/TESTING.md`](docs/TESTING.md) — test categories, validator
  coverage matrix, mutation-test policy
- [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) — adversaries, threats,
  mitigations
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — runtime monitoring,
  incident response
- [`docs/RELEASE.md`](docs/RELEASE.md) — release procedure and invariants
