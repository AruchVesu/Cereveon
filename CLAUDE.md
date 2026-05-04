# CLAUDE.md

## Project Rules

1. Engine output is the chess source of truth.
2. The LLM explains, but must not override engine truth or bypass ESV.
3. Autonomous RL implementation is prohibited.
4. Tests must remain objective.
5. Never weaken tests or validators to make them pass.
6. Never push before all required validation passes.
7. Prefer minimal, localized, layer-correct changes.
8. The architecture defined in `docs/ARCHITECTURE.md` must not be violated.
9. Commits must describe changes in detail.

## Required Reviews

- Use specialist subagents where appropriate.
- Use `architecture-reviewer` before finishing substantial or cross-layer work.
- Use `devils-advocate` for adversarial security, lifecycle, memory, or interop review when code is risky or externally exposed.
- Preserve API contracts unless docs, tests, and dependent callers are updated together.
- Report blockers explicitly instead of bypassing checks.

## Required Workflow

1. Read this file and the referenced `ARCHITECTURE.md` / `TESTING.md` before editing.
2. Use Explore or equivalent read-only inspection first to find the relevant code paths.
3. Summarize the affected modules and propose a minimal plan before making changes.
4. Modify only the necessary files in the correct layer.
5. Run the relevant validation and fix failures when possible.
6. Finish with the required checks for the layers you touched.

## Subagent Routing

- Use `engine-specialist` for engine pool, UCI, JNI bridge, move normalization, and evaluation semantics.
- Use `backend-coach-specialist` for API routes, coaching pipeline, auth, RAG assembly, and backend integration behavior.
- Use `android-specialist` for Android UI, API client integration, and Gradle-backed validation.
- Use `test-writer` for unit, integration, regression, and contract coverage.
- Use `devils-advocate` for hostile-input, security, memory, coroutine/lifecycle, and cross-language boundary audits.
- Use `architecture-reviewer` for read-only compliance review before closing substantial work.

## Required Checks

- Backend edits should trigger backend-safe checks.
- Android edits should trigger Gradle validation.
- Finishing a task should trigger the configured stop hooks before Claude exits.

## Repo Map

- `llm/`: backend coaching, API, RAG, auth, SECA flows, and backend tests
- `android/`: Android client and Gradle validation surface
- `engine/`: native engine code and engine-side experiments
- `docs/`: architecture, testing, operations, and release references
- `design/`: React/Babel design canvas mockups (Atrium screens) — visual prototype only, not part of the build
- `.claude/agents/`: project subagents
- `.claude/hooks/`: deterministic governance hooks

## References

- `docs/ARCHITECTURE.md`
- `docs/TESTING.md`

## Developer Setup

> Full instructions: `CLAUDE.md` covers AI agent rules. New developers and human contributors should start here.

### Quick start (Docker)

```bash
cp .env.example .env      # edit values if needed
docker compose up
```

API at `http://localhost:8000`. Requires [Ollama](https://ollama.ai) on the host:

```bash
ollama pull qwen2.5:7b-instruct-q2_K
ollama serve
```

`host.docker.internal` is mapped automatically on all platforms (macOS, Windows, Linux) via `extra_hosts` in `docker-compose.yml`.

### Dev Container (VS Code)

Open the repo and click **"Reopen in Container"**. Installs Python 3.13, Node.js 22, Stockfish, and all Python deps automatically. Ollama must still run on the host.

### Android setup

`android/local.properties` is gitignored and machine-specific. Generate it once:

```bash
# macOS / Linux / WSL
./scripts/setup-android.sh

# Windows (PowerShell)
"sdk.dir=$($env:LOCALAPPDATA -replace '\\','/')/Android/Sdk" > android\local.properties
```

Or open `android/` in Android Studio — it generates the file automatically.

The app builds for `arm64-v8a` (physical devices, Apple Silicon emulators) and `x86_64` (Intel/AMD AVDs).

### Python API (bare-metal)

```bash
sudo apt install stockfish   # or: brew install stockfish
pip install -r llm/requirements.txt
cp .env.example .env
python -m uvicorn llm.server:app --host 0.0.0.0 --port 8000
```

Stockfish is auto-detected via `PATH`, then falls back to `/usr/games/stockfish` (Linux) or `engines/stockfish.exe` (Windows). Override with `STOCKFISH_PATH` in `.env`.

### Environment variables

See `.env.example` for the full reference. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SECA_API_KEY` | `dev-key` | Auth key; any value works in `dev` mode |
| `SECA_ENV` | `dev` | `dev` or `prod` |
| `COACH_OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `COACH_OLLAMA_MODEL` | `qwen2.5:7b-instruct-q2_K` | LLM model |
| `STOCKFISH_PATH` | auto-detected | Override Stockfish binary path |
| `REDIS_URL` | *(unset)* | Redis for move cache; omit for in-memory only |

### Running tests

```bash
python llm/run_ci_suite.py                    # Python CI suite
python llm/run_quality_gate.py black          # formatting
python llm/run_quality_gate.py pylint         # linting
python llm/run_quality_gate.py mypy           # types
cd android && ./gradlew test                  # Android host JVM tests
cd android && ./gradlew connectedAndroidTest  # Android instrumented tests
```

### Common issues

| Symptom | Fix |
|---------|-----|
| `UnsatisfiedLinkError: libchessengine.so` | Expected on host JVM — `NativeEngineProvider` returns `null` gracefully. Real test runs on device/emulator via `connectedAndroidTest`. |
| `FileNotFoundError: stockfish` | Install Stockfish or set `STOCKFISH_PATH` in `.env`. |
| `host.docker.internal` unreachable on Linux | `docker-compose.yml` adds `host-gateway` automatically. For bare `docker run`, add `--add-host=host.docker.internal:host-gateway`. |
| `sdk.dir` Gradle error | Run `./scripts/setup-android.sh` or open `android/` in Android Studio. |
