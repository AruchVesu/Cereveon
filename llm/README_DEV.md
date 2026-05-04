Local dev: using Fake LLM

You can run the embedded API locally without a real LLM by using the FakeLLM.
Set the `LLM_MODEL` env var to `fake` or `fake:<mode>` before running scripts.

Examples:
- PowerShell (temporary for session):
  $Env:LLM_MODEL = 'fake:mate_softening'
  python test_explain.py

- Command Prompt (temporary):
  set LLM_MODEL=fake:mate_softening && python test_explain.py

Supported fake modes (examples):
- `compliant` — returns a compliant explanation
- `forbidden_phrase` — returns text containing forbidden phrases
- `missing_data_violation` — returns text missing required missing-data acknowledgements
- `mate_softening` — returns text that softens mate claims

This is useful for iterating tests and debugging `run_mode_2` without depending on external LLM services.

Optional CI-only real-LLM test

- The repository includes an optional test that runs a representative case against a real LLM. It is **skipped by default**.
- To enable it in CI or locally, set:
  - `RUN_REPR_CI=1` and ensure `LLM_MODEL` is set to your real model name.

Examples:
- PowerShell (temporary):
  $Env:RUN_REPR_CI = '1'; $Env:LLM_MODEL = 'qwen2.5:7b-instruct-q2_K'; python -m pytest -q rag/tests/test_ci_optional_run.py
- Command Prompt (temporary):
  set RUN_REPR_CI=1 && set LLM_MODEL=qwen2.5:7b-instruct-q2_K && python -m pytest -q rag/tests/test_ci_optional_run.py

Use this to verify end-to-end behavior when a real model is available.

Docker runtime for low-latency Stockfish

- A pooled Stockfish runtime is available via `docker-compose.yml` and `Dockerfile.api`.
- It pre-spawns engine workers, keeps them alive, and uses Redis FEN caching.
- The lightweight `/engine/eval` runtime in `host_app.py` now defaults to `ORJSONResponse`, supports both `POST /engine/eval` and `GET /engine/eval?fen=...`, and is intended to be started with multiple uvicorn workers.

Start:
- `docker compose up --build`

Host app start:
- `uvicorn host_app:app --host 0.0.0.0 --port 8000 --workers 4`
- `python host_app.py`

Host app performance notes:
- Install `orjson` from `requirements.txt`; `host_app.py` will use `ORJSONResponse` automatically when available.
- For latency-sensitive load tests, prefer `GET /engine/eval?fen=<encoded>&nodes=3000` over JSON `POST`, or omit both limits to use the node-limited fast default.
- `host_app.py` can use a Polyglot opening book before Redis or engine evaluation. Put a book file at `books/performance.bin` in the project root or override `OPENING_BOOK_PATH`.
- If you do not have a real book file yet, generate the repo's small dev book with `python llm/scripts/generate_dev_polyglot_book.py`.
- Opening-book hits return immediately with `source=book`, skip engine work, and also populate Redis with the book result.
- `host_app.py` now expects real Redis and will fail startup if `PING` fails.
- In WSL dev setups, install and start Redis with `apt install redis-server`, then point the app at `REDIS_URL=redis://localhost:6379/0`.
- Use `REDIS_MAX_CONNECTIONS` to raise or lower the async client pool; the default is `50`.
- `REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS` and `REDIS_SOCKET_TIMEOUT_SECONDS` default to `1.0` so startup fails quickly when Redis is down or unreachable.
- `REDIS_PING_TIMEOUT_SECONDS` defaults to `2.0` and caps the startup health check itself.
- `ENGINE_POOL_SIZE` is per worker. With `--workers 4`, a pool size of `2` means up to `8` Stockfish processes. Raise it only when miss-heavy concurrency shows `engine_wait_ms` climbing.

Main env knobs:
- `ENGINE_POOL_SIZE` (default `2`)
- `ENGINE_THREADS` (default `1`)
- `ENGINE_HASH_MB` (default `16`)
- `ENGINE_NODES` (default `5000`)
- `ENGINE_DEFAULT_NODES` (default `5000`)
- `ENGINE_TRAINING_NODES` (default `5000`)
- `ENGINE_ANALYSIS_NODES` (default `5000`)
- `ENGINE_BLITZ_NODES` (default `5000`)
- `ENGINE_DEFAULT_MOVETIME_MS` (default `40`)
- `ENGINE_TRAINING_MOVETIME_MS` (default `40`)
- `ENGINE_ANALYSIS_MOVETIME_MS` (default `80`)
- `ENGINE_BLITZ_MOVETIME_MS` (default `25`)
- `ENGINE_QUEUE_TIMEOUT_MS` (default `0`; non-blocking acquire, immediate fallback on pool exhaustion)
- `ENGINE_ASYNC_PREDICT_ENABLED` (default `1`; async next-position cache fill)
- `ENGINE_ASYNC_PREDICT_PLIES` (default `2`; predictive depth)
- `ENGINE_ASYNC_PREDICT_MOVETIME_MS` (default `20`; predictive fast eval)
- `ENGINE_ASYNC_PREDICT_NODES` (default `5000`; predictive search budget)
- `ENGINE_ASYNC_PREDICT_TOP_K` (default `3`; branch factor for predictive top-move precompute)
- `ENGINE_HEURISTIC_ENABLED` (default `1`; tier-0 opening/cheap heuristic shortcut)
- `ENGINE_HEURISTIC_MAX_FULLMOVE` (default `8`; early-game cutoff for heuristic tier)
- `ENGINE_HEURISTIC_ELO_MAX` (default `1600`; heuristic tier for lower-strength opponents outside blitz)
- `ENGINE_DEEP_REFINE_ENABLED` (default `1`; async tier-3 deep refinement)
- `ENGINE_DEEP_REFINE_NODES` (default `12000`; deep-refine node budget)
- `MOVE_CACHE_TTL_SECONDS` (default `3600`)
- `MOVE_CACHE_L1_MAX_ITEMS` (default `500`; bounded in-memory hot cache)
- `OPENING_BOOK_ENABLED` (default `1`)
- `OPENING_BOOK_PATH` (default `books/performance.bin` in the project root, with fallback to `llm/books/performance.bin`)
- `OPENING_BOOK_SCORE` (default `20`)
- `OPENING_BOOK_SELECTION` (default `best`; use `weighted` for randomized book choices)
- `ENGINE_PREWARM_MODES` (default `blitz`; comma-separated)
- `ENGINE_PREWARM_FENS` (default includes `startpos`, `1.e4 e5`, `1.d4 d5`; override with `||`-separated FENs)
