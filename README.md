# Cereveon

AI-powered chess coaching system enforcing strict architectural separation between move generation, position evaluation, and natural-language explanation.

## System Overview

Cereveon is a source-available mono-repository (see [LICENSE.md](LICENSE.md)) containing four integrated layers:

- **Android App** — UI, gameplay orchestration, and coaching display
- **C++ Opponent Engine** — ~1800 Elo search via JNI bridge
- **Stockfish Engine Pool** — pooled analysis instances providing position evaluation (distinct from the opponent engine)
- **LLM Explanation System (Mode-2)** — RAG-grounded, contract-validated explanations backed by SECA safety enforcement

The system enforces non-negotiable role invariants: the opponent never explains, Stockfish never plays, the LLM never calculates.

## Core Architecture

### Architectural Roles

```
Moves are facts.
Evaluations are judgments.
Explanations are commentary.

No component is allowed to blur these roles.
```

### Design Invariants

| Invariant | Enforcement |
|-----------|-------------|
| Opponent engine never explains | Layer isolation |
| Stockfish never selects moves | Separate pool |
| LLM never calculates or suggests moves | Output validators |
| Explanations generated only after moves are committed | Pipeline ordering |
| No decision-making component depends on LLM output | Trust boundary |

## Mode-2 Pipeline

The explanation subsystem is designated **Mode-2**: a non-calculating chess explainer. No step may be skipped or reordered.

![Cereveon Mode-2 pipeline — Android client → API → engine truth → ESV → RAG → prompt → LLM (untrusted, amber) → validators → response. Trust boundaries are dashed amber lines.](docs/architecture-diagram.svg)

For the formal specification of each layer and its invariants, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
Stockfish JSON (ground truth)
        ↓
Engine Signal Extraction (ESV)          ← deterministic, trust boundary
        ↓
RAG Retrieval                           ← deterministic, rule-based (no embeddings)
        ↓
Prompt Rendering (Mode-2)               ← fixed injection order, golden-tested
        ↓
LLM Generation                          ← untrusted, stochastic
        ↓
Output Validation                       ← hard gate, never bypassed
        ↓
Bounded Retry (≤ 2 retries)            ← quality only, not safety
        ↓
Final Response
```

### Engine Signal Vector (ESV)

The ESV is the normalized, loss-limited representation of Stockfish output. It is the sole engine-derived input permitted downstream of the evaluator.

Properties:
- Extracted deterministically from raw Stockfish JSON
- Coarsened to bands — no raw centipawn scores or numeric precision
- No move lists, no principal variations, no search metadata
- Downstream components receive the ESV and nothing else from the engine

### RAG Retrieval

Document selection is deterministic and rule-based. There is no embedding similarity, no semantic search, and no vector database. Retrieval conditions are explicit: documents are selected based solely on ESV values. The document corpus covers tactics, pawn structure, endgame principles, positional concepts, and meta-coaching topics.

**Trade-off (deterministic vs. semantic retrieval).** Rule-based retrieval is the design choice that makes the rest of this system auditable: every document selection is reproducible from the ESV, golden-testable, and inspectable line-by-line in `llm/rag/retriever/rule_matcher.py`. The cost is recall on positions whose ESV signature matches no rule in the document corpus — that case is real, not hypothetical, and the failure is observable: `retrieve()` returns an empty list, `render_mode_2_prompt` substitutes the placeholder string `(no retrieved context)` into the RAG section, and the LLM is forced to operate against the system prompt and engine signal alone without supporting positional concepts. We chose this fallback (degraded but not refused) deliberately — refusing to respond would punish the user for the corpus' gaps; the validators downstream (output firewall + Mode-2 contracts) still enforce the safety claims regardless of how thin the context block is. The mitigation for sustained empty-retrieval rates is corpus expansion, never a switch to semantic search.

### LLM Layer

The LLM implements `BaseLLM.generate(prompt: str) -> str` — no additional methods are permitted. It may rephrase, explain, and contextualize. It may not reason beyond provided inputs, introduce new facts, or contradict the engine evaluation. The LLM is always treated as untrusted.

### Output Validation

All LLM outputs pass contract validation before being returned. Validation is a hard gate — failure stops execution immediately and is never bypassed.

Enforced contracts:
- No engine tool mentions
- No move suggestions or algebraic notation
- No invented tactics
- Correct forced-mate handling (inevitability emphasized, no long-term planning)
- Explicit refusal when required engine data is missing

### Bounded Retries

Retries exist only to improve explanation *quality*, not to recover from safety violations. Hard limits:

| Parameter | Value |
|-----------|-------|
| Maximum retries | 2 |
| Total attempts | 3 |
| Prompt changes between retries | Not allowed |
| Temperature changes | Not allowed |
| Validator bypass | Not allowed |

A retry is triggered only when output passes validation but scores below the quality threshold. Any validation failure is a hard stop — no retry.

## SECA Safety Enforcement

The **SECA** (Safety-Enforced Coaching Architecture) safety layer is enforced at server startup via `llm/seca/safety/freeze.py`. The `SafeWorldModel` is instantiated and passed to `enforce()` before any request is served.

Runtime guarantees:
- No online training
- No bandit updates
- No world model learning
- No background adaptive loops
- Deterministic runtime (non-determinism isolated to LLM generation only)

`GET /seca/status` returns the current runtime safety flags. The Android client reads this at cold-start to confirm `safe_mode: true` before sending coaching requests.

## Trust Boundaries

| Component | Trust Level | Deterministic |
|-----------|-------------|---------------|
| Stockfish JSON | Trusted | Yes |
| Engine Signal (ESV) | Trusted | Yes |
| RAG Documents | Trusted | Yes |
| Prompt Renderer | Trusted | Yes |
| LLM Output | Untrusted | No |
| Output Validators | Trusted | Yes |

Non-determinism is strictly isolated to LLM generation.

## API Reference

The backend exposes a FastAPI application (`llm/server.py`). All endpoints require `X-Api-Key` authentication unless noted. Rate limiting is applied per-IP via `slowapi`. Request bodies are capped at 512 KB.

### Authentication

Authentication uses two layers:
1. **API key** — `X-Api-Key: <SECA_API_KEY>` header on protected endpoints
2. **JWT session tokens** — issued by `/auth/*` routes for player sessions (`Authorization: Bearer <token>`)

In `SECA_ENV=dev`, the API key check is bypassed. In `SECA_ENV=prod`, `SECA_API_KEY` must be set or the server refuses to start.

### Endpoints

| Method | Path | Auth | Rate limit | Description |
|--------|------|------|-----------|-------------|
| GET | `/` | — | — | Liveness probe |
| GET | `/health` | — | — | Health check |
| GET | `/seca/status` | — | — | SECA safety flags |
| GET | `/debug/engine` | API key | — | Engine pool depth |
| POST | `/move` | JWT | 30/min | Request opponent move |
| POST | `/live/move` | JWT | 30/min | Real-time coaching hint on player move |
| POST | `/analyze` | API key | 30/min | Engine signal only (no LLM) |
| POST | `/explain` | API key | — | Full Mode-2 explanation |
| POST | `/chat` | API key | 10/min | Multi-turn coaching conversation |
| POST | `/chat/stream` | API key | 10/min | SSE-streamed coaching conversation |
| POST | `/explanation_outcome` | API key | 20/min | Report post-explanation learning outcome |

### `POST /move`

Requests the opponent engine to select a move for a given position.

```json
{
  "fen": "<FEN or 'startpos'>",
  "moves_uci": ["e2e4", "e7e5"],
  "mode": "default | blitz | analysis | training",
  "movetime_ms": 40
}
```

Response includes `uci`, `san`, `opponent_elo`, `cache_hit`, and telemetry (`latency_ms`, `engine_time_ms`, `cache_hit_rate`, `queue_depth`).

The engine pool maintains a two-level move cache (in-memory L1, optional Redis L2). Cache hits bypass engine computation entirely. Predictive pre-caching runs asynchronously after each move to warm follow-up positions.

Opponent Elo is computed dynamically via the adaptation layer based on the authenticated player's rating and confidence.

### `POST /live/move`

Generates a real-time coaching hint immediately after a player move, before the opponent responds. Returns `hint`, `engine_signal`, `move_quality`, and `mode`.

### `POST /analyze`

Returns the ESV for a position without invoking the LLM.

```json
{
  "fen": "<FEN>",
  "stockfish_json": { ... },
  "user_query": ""
}
```

### `POST /explain`

Full Mode-2 pipeline: ESV → RAG → prompt → LLM → validate → return.

```json
{
  "fen": "<FEN>",
  "stockfish_json": { ... },
  "user_query": "Why is this position difficult?"
}
```

Response:
```json
{
  "explanation": "...",
  "engine_signal": { ... },
  "mode": "SAFE_V1"
}
```

User queries are sanitized by `input_sanitizer.sanitize_user_query()` before reaching the LLM (injection protection). Schema validation also occurs at the HTTP boundary (FastAPI) as independent defence-in-depth.

### `POST /chat` and `POST /chat/stream`

Long-form coaching conversation over a full message history. `/chat/stream` returns Server-Sent Events:

```
data: {"type": "chunk", "text": "word "}
...
data: {"type": "done", "engine_signal": {...}, "mode": "CHAT_V1"}
```

Chat history is capped at 50 turns. The pipeline always grounds responses in the engine evaluation — no free-form reasoning occurs.

### Security Response Headers

All responses include:
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`

CORS is configured via `CORS_ALLOWED_ORIGINS`. If unset, all cross-origin requests are blocked.

## Stockfish Engine Pool

The pool is initialized at startup via `StockfishEnginePool`. Key parameters:

| Variable | Default | Description |
|----------|---------|-------------|
| `ENGINE_POOL_SIZE` | 8 | Concurrent Stockfish processes |
| `ENGINE_THREADS` | 1 | Threads per process |
| `ENGINE_HASH_MB` | 128 | Hash table per process (MB) |
| `ENGINE_SKILL_LEVEL` | 10 | UCI skill level |
| `ENGINE_DEFAULT_MOVETIME_MS` | 40 | Default search time |
| `ENGINE_ANALYSIS_MOVETIME_MS` | 80 | Analysis mode search time |
| `ENGINE_BLITZ_MOVETIME_MS` | 25 | Blitz mode search time |

Move cache TTL is controlled by `MOVE_CACHE_TTL_SECONDS` (default 3600 s). Optional Redis backing via `REDIS_URL`.

Cache pre-warming runs at startup against configurable FEN positions (`ENGINE_PREWARM_FENS`) and modes (`ENGINE_PREWARM_MODES`). Default pre-warm set includes starting position and common early opening positions.

## Adaptation Layer

`compute_adaptation(player.rating, player.confidence)` returns per-request opponent Elo and teaching style parameters. The opponent Elo is derived from authenticated player data — not a static configuration value. Teaching style (explanation tone) adjusts based on the same inputs.

This is a **heuristic, subject to revision** — labelled here so it is not mistaken for a learned policy. The current formula (in `llm/seca/adaptation/{skill_profile,teaching_policy,opponent_policy}.py`):

```
r = clamp((rating − 400) / 2000, 0, 1)               # rating ∈ [400, 2400] → r ∈ [0, 1]
explanation_depth   = r
concept_complexity  = r ** 1.2
opponent_strength   = r
opponent_human_error = 1 − r

target_elo        = int(600 + opponent_strength * 1800)        # ∈ [600, 2400]
human_error_rate  = opponent_human_error * 0.25                # ∈ [0, 0.25]
teaching_style    = simple        if explanation_depth < 0.3   # rating < 1000
                  | intermediate  if explanation_depth < 0.7   # rating < 1800
                  | advanced      otherwise
```

Note that `confidence` is in the function signature but is **not currently consumed** by the formula — it is a forward-compatible hook for a future variance-aware adaptation. Any change to incorporate it (or to retune the constants 400/2000/1800/0.25/0.3/0.7) is an explicit policy change, must be motivated in the commit, and requires updating `test_adaptive_engine_wiring.py`'s pinned ELO range.

## Telemetry

Explanation quality scores are recorded to `telemetry/quality_scores.jsonl` (append-only). Each record contains:

```json
{"timestamp": "...", "score": 8, "case_type": "...", "model": "...", "mode": "...", "attempt": 1}
```

No prompt text, no FEN, no user data is stored. The file is never read back at runtime — telemetry is operational instrumentation only.

Healthy quality distribution: majority 8–9, occasional 7, rare ≤ 6. Sustained downward drift or score clustering near threshold indicates model instability.

## Repository Structure

```
├── android/                  # Android application (UI, game orchestration)
├── engine/                   # C++ opponent engine (~1800 Elo, JNI bridge)
├── llm/
│   ├── server.py             # FastAPI application entry point
│   ├── explain_pipeline.py   # Mode-2 pipeline with bounded retries
│   ├── engine_pool.py        # Stockfish pool management
│   ├── rag/
│   │   ├── engine_signal/    # ESV extraction
│   │   ├── retriever/        # Deterministic document retrieval
│   │   ├── prompts/          # Mode-2 prompt templates (golden-tested)
│   │   ├── validators/       # Output contracts
│   │   └── tests/            # Golden, contract, regression, quality tests
│   ├── seca/
│   │   ├── safety/           # freeze.py — SECA enforcement at startup
│   │   ├── auth/             # JWT authentication
│   │   ├── curriculum/       # Skill-based curriculum scheduling
│   │   ├── adaptation/       # Opponent Elo and teaching style adaptation
│   │   ├── coach/            # Chat and live-move coaching pipelines
│   │   ├── storage/          # PostgreSQL-backed event store and repo
│   │   └── engines/          # Engine adapters
│   └── tests/                # API contract and pipeline regression tests
└── docs/
    ├── ARCHITECTURE.md       # Formal system specification
    ├── TESTING.md            # Test strategy and validator rules
    ├── OPERATIONS.md         # Production operation guide
    ├── OPERATIONS_RETRIES.md # Bounded retry policy
    └── RELEASE.md            # Mandatory release procedure
```

## Testing

The project uses five test categories. All layers are covered — no layer is unprotected.

| Category | Scope | CI | Command |
|----------|-------|----|---------|
| A — Golden tests | ESV mapping, RAG retrieval, prompt snapshots | Yes | `pytest llm/rag/tests/golden/` |
| B — Contract tests | Forbidden patterns, mate handling, missing data (Fake LLM) | Yes | `pytest llm/rag/tests/contracts/` |
| C — Smoke test | Real LLM connectivity, output passes validators | No (local only) | `pytest llm/rag/tests/llm/test_ollama_smoke.py` |
| D — Regression tests | Repeated real LLM runs, contract compliance over time | No (on change events) | `pytest llm/rag/tests/llm/test_llm_regression.py` |
| E — Quality heuristics | Length, sentence structure, non-triviality | No (advisory) | `pytest llm/rag/tests/quality/` |
| F — Validator mutation | mutmut against `llm/rag/validators/` — does the test fail when the validator is logically wrong? | No (local, on-demand) | `bash scripts/run_mutation_tests.sh` |

The **Fake LLM** is a mandatory test component that simulates contract violations to prove validator enforcement. It is not optional.

### Code-quality gates (precise, not vague)

"Mypy passes" and "Pylint passes" are commitments only as strict as the
config behind them. The actual scope and rule set in this repo:

- **Black** — formatter. Target: `py313`, line length `100`. Scope: 22-file
  whitelist defined in `llm/run_quality_gate.py:FORMAT_TARGETS` (the
  stable Python surface — dormant SECA research subtrees are deliberately
  excluded; see `pyproject.toml [tool.black] extend-exclude` for the list
  and rationale).
- **Pylint** — `pylint --score=n` on `PYLINT_TARGETS` (20 files). Default
  rule set MINUS the permissive softeners enumerated in `pyproject.toml`
  `[tool.pylint."messages control"] disable`: `broad-exception-caught`,
  `line-too-long` (covered by Black), `missing-class-docstring`,
  `missing-function-docstring`, `missing-module-docstring`,
  `subprocess-run-check`, `too-few-public-methods`, `too-many-arguments`,
  `too-many-instance-attributes`, `too-many-locals`. Every other Pylint
  category is enforced.
- **Mypy** — `mypy` on `MYPY_TARGETS` (16 files). Config from
  `pyproject.toml [tool.mypy]`: `python_version = "3.13"`,
  `ignore_missing_imports = true` (external libraries without type stubs
  are silenced — internal modules are NOT silenced),
  `warn_unused_configs = true`. **Not** `--strict`: the typed utility
  surface annotates aggressively but does not require every function in
  the codebase to be annotated. Mypy errors on annotated functions are
  fail-on, untyped function bodies are not deep-checked
  (`--check-untyped-defs` is not enabled).
- **Coverage** — global ≥ 80% via `pytest --cov-fail-under=80`, AND
  per-module floors enforced by `llm/check_coverage_thresholds.py` after
  the pytest run: validators (`llm/rag/validators/*.py`) and the
  post-LLM safety firewall (`llm/rag/safety/*.py`) MUST stay ≥ 95%.
  Three legacy modules (`contextual_bandit`, `curriculum/spacing`,
  `skills/trainer`) carry documented exemptions at their current
  coverage; each is a TODO to lift.
- **`pip-audit`** — strict mode against `llm/requirements.txt` AND
  `llm/requirements-ci.txt` (closed CI/runtime split documented in the
  workflow); any unfixed CVE blocks the merge.
- **Trivy** — image scan on the published GHCR images. CRITICAL
  unfixed → block; HIGH/CRITICAL → SARIF upload to the Security tab.

To extend any of these (e.g. enable `mypy --strict` on a specific
package, or drop a Pylint disable), update the relevant config in
`pyproject.toml` and the target list in `llm/run_quality_gate.py` —
both surfaces must agree, and the change is a contract update worth a
deliberate commit message.

### Running Tests

```bash
python llm/run_ci_suite.py                                         # full CI suite
python -m pytest -q llm/rag/tests/golden/test_retriever.py
python -m pytest -q llm/rag/tests/golden/test_prompt_snapshot.py
python -m pytest -q llm/rag/tests/contracts/test_fake_llm.py
python -m pytest -q llm/tests/test_api_contract_validation.py
python -m pytest -q llm/tests/test_coaching_pipeline_regression.py
python llm/run_quality_gate.py black                               # formatting
python llm/run_quality_gate.py pylint                              # linting
python llm/run_quality_gate.py mypy                                # types
```

LLM regression tests (Category D) must be run before every release, after any system prompt or RAG document change, and after any model update. They must never run in CI.

## Developer Setup

### Quick Start (Docker)

```bash
cp .env.example .env      # edit values if needed
docker compose up
```

API at `http://localhost:8000`. Requires [Ollama](https://ollama.ai) on the host:

```bash
ollama pull qwen2.5:7b-instruct-q2_K
ollama serve
```

`host.docker.internal` is mapped automatically on macOS, Windows, and Linux via `extra_hosts` in `docker-compose.yml`.

### Dev Container (VS Code)

Open the repo and select **"Reopen in Container"**. Installs Python 3.13, Node.js 22, Stockfish, and all Python dependencies automatically. Ollama must run on the host.

### Bare-Metal Python

```bash
sudo apt install stockfish   # or: brew install stockfish
pip install -r llm/requirements.txt
cp .env.example .env
python -m uvicorn llm.server:app --host 0.0.0.0 --port 8000
```

Stockfish is auto-detected via `PATH`, then falls back to `/usr/games/stockfish` (Linux) or `engines/stockfish.exe` (Windows). Override with `STOCKFISH_PATH` in `.env`.

### Android Setup

`android/local.properties` is gitignored and machine-specific:

```bash
# macOS / Linux / WSL
./scripts/setup-android.sh

# Windows (PowerShell)
"sdk.dir=$($env:LOCALAPPDATA -replace '\\','/')/Android/Sdk" > android\local.properties
```

Or open `android/` in Android Studio — it generates the file automatically.

Builds target `arm64-v8a` (physical devices, Apple Silicon emulators) and `x86_64` (Intel/AMD AVDs).

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECA_API_KEY` | `dev-key` | Auth key (any value works in `dev` mode; required in `prod`) |
| `SECA_ENV` | `dev` | `dev` or `prod` |
| `SECRET_KEY` | — | JWT signing secret (≥ 32 chars; required in `prod`) |
| `COACH_OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `COACH_OLLAMA_MODEL` | `qwen2.5:7b-instruct-q2_K` | LLM model. Tags are fine for dev (Ollama resolves whatever digest is current), but **production should pin the digest** with `model:tag@sha256:...` so a re-pushed tag upstream cannot silently change behaviour under the LLM regression contract. Find the digest with `ollama show <tag> --modelfile \| grep '^FROM'`; full recipe in `.env.prod.example`. |
| `STOCKFISH_PATH` | auto-detected | Override Stockfish binary path |
| `REDIS_URL` | *(unset)* | Redis for move cache L2; omit for in-memory only |
| `ENGINE_CACHE_VERSION` | *(unset)* | Optional move-cache flush override. The L1+L2 cache key already incorporates a fingerprint of the engine config (`ENGINE_SKILL_LEVEL`, `ENGINE_THREADS`, `ENGINE_HASH_MB`, all movetime defaults, the Stockfish binary path), so changing any of those automatically invalidates stale entries. Set this to any string (e.g. `post-upgrade-2026-05`) to force a flush without changing those fields — useful when you replace the Stockfish binary in place at the same path. |
| `SECA_INSECURE_DEV` | *(unset → `false`)* | Local-development opt-in for the no-`SECA_API_KEY` auth bypass. With `SECA_ENV=dev` and no key configured, requests to protected endpoints are rejected with HTTP 401 by default; set this to `true` to pass them through. **Never set in production** — see [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) § T6. |
| `DATABASE_URL` | — | PostgreSQL DSN (required in `prod`) |
| `CORS_ALLOWED_ORIGINS` | *(unset — blocks all cross-origin)* | Comma-separated allowed origins |
| `ENGINE_POOL_SIZE` | `8` | Concurrent Stockfish processes |
| `ENGINE_ASYNC_PREDICT_ENABLED` | `true` | Enable predictive move pre-caching |

See `.env.example` for the full reference.

### Common Issues

| Symptom | Fix |
|---------|-----|
| `UnsatisfiedLinkError: libchessengine.so` | Expected on host JVM — `NativeEngineProvider` returns `null` gracefully. Run on device/emulator via `connectedAndroidTest`. |
| `FileNotFoundError: stockfish` | Install Stockfish or set `STOCKFISH_PATH` in `.env`. |
| `host.docker.internal` unreachable on Linux | `docker-compose.yml` adds `host-gateway` automatically. For bare `docker run`, add `--add-host=host.docker.internal:host-gateway`. |
| `sdk.dir` Gradle error | Run `./scripts/setup-android.sh` or open `android/` in Android Studio. |
| Server refuses to start in prod | `SECA_API_KEY` and `SECRET_KEY` must be set when `SECA_ENV=prod`. |

## CI/CD

GitHub Actions (`fly-deploy.yml`) runs on pull requests and pushes to `main`, and on `v*.*.*` tag push.

Jobs:
1. **actionlint** — workflow YAML validation
2. **python-tests** — golden tests, contract tests (incl. validator violations corpus), API contract validation, pipeline regression, explain schema validation, full CI suite, Black/Pylint/Mypy gates (see *Code-quality gates* above for exact scope and strictness), per-module coverage floors (validators ≥ 95%, rest ≥ 80%), pip-audit, Trivy
3. **android-build** — Gradle build and host JVM test suite
4. **docker-build** — builds `cereveon` (Fly.io edge / Node) and `cereveon-llm-api` (Hetzner backend / Python) images
5. **deploy** — triggered on `v*.*.*` tag; publishes to GHCR and deploys to production

CI never runs real LLM inference, never requires Ollama, and never depends on telemetry.

## Release Process

Releases follow `vMAJOR.MINOR.PATCH` (monotonically increasing):
- `MAJOR` — architectural or contract changes
- `MINOR` — new features, RAG documents, golden cases
- `PATCH` — bug fixes, wording improvements, no behavior change

Pre-release checklist (non-negotiable):
1. Clean working tree (`git status`)
2. All CI-safe tests pass (golden, contract, API contract, pipeline regression)
3. LLM regression tests pass (`test_llm_regression.py`)
4. Real LLM smoke test passes (`test_ollama_smoke.py`)
5. Manual output sanity review (no engine mentions, no move suggestions, correct tone)

Pushing a `vX.Y.Z` tag publishes the GitHub Release and GHCR images for both `cereveon:vX.Y.Z` and `cereveon-llm-api:vX.Y.Z`.

## Architecture Constraints

The following changes are explicitly **forbidden**:
- Weakening output validators
- Bypassing or replacing the ESV
- Dynamic prompt mutation at runtime
- LLM reasoning beyond provided inputs
- Autonomous RL implementation
- Disabling or skipping SECA enforcement

**Allowed** without architectural review:
- Adding RAG documents
- Adding golden test cases
- Improving explanation wording (within existing contracts)
- Adding new `BaseLLM` adapters

## Design Philosophy

Cereveon prioritizes:

1. **Correctness** — Invariants enforced via code and test contracts, not convention
2. **Determinism** — All layers except LLM generation are fully reproducible
3. **Non-hallucination** — ESV normalization and output validators guarantee the LLM cannot invent engine facts
4. **Safety** — Strict contracts on all LLM outputs; no output is always better than unsafe output
5. **Maintainability** — Loose coupling with explicit trust boundaries at every layer

Over convenience, feature velocity, or explanation quality at the expense of correctness.

## Further Reading

- `docs/ARCHITECTURE.md` — Formal system specification, trust boundaries, data flow
- `docs/TESTING.md` — Test strategy, validator rules, all five test categories
- `docs/THREAT_MODEL.md` — Adversaries, threats, mitigations, accepted residual risks
- `docs/OPERATIONS.md` — Production operation, failure modes, incident response
- `docs/OPERATIONS_RETRIES.md` — Bounded retry policy and telemetry interpretation
- `docs/RELEASE.md` — Mandatory release procedure and invariants
- `docs/LICENSE.md` — Rights and attribution
