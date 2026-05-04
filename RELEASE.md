RELEASE.md
Purpose

This document defines the mandatory release procedure for the ChessCoach-AI Mode-2 system.

A release is considered valid only if all requirements in this document are satisfied.

This project is source-available under a custom license; see [LICENSE.md](LICENSE.md).
This document is intended for internal use only.

Release Definition

A release is any version of the system that is:

deployed

distributed

demonstrated

benchmarked

or used outside local development

Ad-hoc usage without following this procedure is not permitted.

Release Authority

Only the project owner (or explicitly authorized collaborators) may perform a release.

Automated systems may assist but do not replace human verification.

Versioning Policy
Version Format
vMAJOR.MINOR.PATCH


Examples:

v1.0.0

v1.1.0

v1.1.1

Rules

MAJOR: architectural or contract changes

MINOR: new features, new RAG documents, new golden cases

PATCH: bug fixes, wording improvements, refactors with no behavior change

Version numbers must be monotonically increasing.

Mandatory Pre-Release Checks (NON-NEGOTIABLE)
1️⃣ Clean Working Tree

Before any release:

git status


Requirement:

No uncommitted changes

No untracked files

If this requirement is not met, the release is invalid.

2️⃣ CI-Safe Test Suite (MANDATORY)

The following tests must pass:

python -m pytest -q llm/rag/tests/golden/test_retriever.py
python -m pytest -q llm/rag/tests/golden/test_prompt_snapshot.py
python -m pytest -q llm/rag/tests/contracts/test_fake_llm.py


Rules:

No failures permitted

No skipped tests permitted

Any failure blocks release

3️⃣ LLM Regression Tests (MANDATORY)

LLM regression tests must be run immediately prior to release.

Command:

python -m pytest -q llm/rag/tests/llm/test_llm_regression.py


Rules:

Must pass without exception

Validators must not be weakened to achieve a pass

Any failure blocks release

4️⃣ Real LLM Smoke Test (MANDATORY)

A real LLM smoke test must be executed.

Command:

python -m pytest -q llm/rag/tests/llm/test_ollama_smoke.py


Rules:

Model must run successfully

Output must pass contract validation

Environment failures invalidate release

5️⃣ Manual Output Sanity Check (MANDATORY)

At least one representative Mode-2 output must be reviewed manually.

Checklist:

No engine mentions

No move suggestions

No invented tactics

Tone is calm and instructional

Behavior matches evaluation type (mate vs cp)

This step may not be skipped.

Prohibited Actions

During release preparation, the following are explicitly forbidden:

Weakening output validators

Disabling contract tests

Skipping regression tests

Adjusting temperature to mask failures

Re-recording golden snapshots to hide regressions

Any of the above invalidates the release.

Release Execution

Once all pre-release checks pass:

1️⃣ Create Git Tag
git tag vX.Y.Z
git push --tags


Tags must correspond exactly to the released version.
Pushing a `vX.Y.Z` tag automatically publishes the GitHub Release and the GHCR images for:

`ghcr.io/<owner>/cereveon:vX.Y.Z`

`ghcr.io/<owner>/cereveon-llm-api:vX.Y.Z`

2️⃣ Record Release Metadata (RECOMMENDED)

Internally record:

version number

model name and version

temperature

date

notable changes

This may be a private log or internal document.

Rollback Policy

If a released version is found to violate contracts:

The release must be considered invalid

Deployment must be reverted

Root cause must be identified

A new PATCH release must be prepared

Silent hotfixes are not permitted.

Release Invariants

If all steps in this document are followed, the released system is guaranteed to be:

deterministic (outside the LLM)

contract-safe

non-hallucinatory

regression-protected

architecture-compliant

Non-Goals

The release process does NOT:

evaluate chess strength

compare model creativity

optimize UX wording

benchmark performance

Those activities are explicitly out of scope.

Enforcement Statement

A deployment that does not follow this document is not a release.

End of RELEASE.md
