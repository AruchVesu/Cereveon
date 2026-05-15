# API Contracts

Authoritative schema contracts for the Chess Coach backend API.
Derived from the production implementation; any deviation constitutes a
**contract mismatch** and must be caught by `test_api_contract_validation.py`.

---

## Conventions

- All endpoints use `Content-Type: application/json`.
- Auth-required endpoints expect `X-Api-Key: <key>` (server.py routes) or
  `Authorization: Bearer <token>` (SECA routes).
- `null` values are allowed for optional fields unless stated otherwise.
- `_metrics` is an internal diagnostic field; clients MUST NOT treat it as
  part of the stable contract (its shape varies by cache source).

---

## 1. `POST /engine/eval`

**Host:** `server.py`
**Auth:** `X-Api-Key` required
**Rate limit:** 30 / minute

Migrated from `host_app.py` in the host_app retirement pass.  Contract narrowed
during the migration — see "Removed in 2026-05-12" below.

### Request body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `fen` | `string` | yes | Full 6-field FEN.  Validated server-side via `chess.Board(...)`; invalid input returns 400. |

### Response

```json
{
  "score":     <int | null>,
  "best_move": <string | null>,
  "source":    <"engine" | "unavailable">
}
```

| Field | Type | Notes |
|-------|------|-------|
| `score` | `int \| null` | Centipawns from White's perspective. Positive = White better. Mate is reported as `±10000`. `null` when the engine pool is unavailable. |
| `best_move` | `string \| null` | Best move in UCI notation (e.g. `"e2e4"`). `null` when no legal moves or engine unavailable. |
| `source` | `string` | `"engine"` on the happy path; `"unavailable"` when the Stockfish pool is down or saturated (the route degrades to a 200-with-nulls rather than 500 to match the Android client's `engineAvailable=false` fallback in `ChessViewModel.dispatchEngineEval`). |

### Removed in 2026-05-12 (host_app retirement)

The pre-migration contract supported a `GET /engine/eval` variant and
`moves` / `movetime_ms` / `nodes` body fields.  None of these were used by
any in-tree caller (the Android `HttpEngineEvalClient` only POSTs `{"fen": ...}`),
and `host_app.py` was never actually deployed to production (the legacy
`llm/Dockerfile` that ran it was orphaned years before the
`llm/Dockerfile.api` split).  The simpler contract above matches what the
Android client actually sends; adding any of the removed fields back is a
contract widening that requires an Android client update in the same release.

---

## 2. `GET /next-training/{player_id}`

**Host:** `server.py`
**Auth:** `X-Api-Key` required

### Path params

| Field | Type | Description |
|-------|------|-------------|
| `player_id` | `string` | Player identifier |

### Response

```json
{
  "topic":         <string>,
  "difficulty":    <float>,
  "format":        <string>,
  "expected_gain": <float>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `topic` | `string` | Training topic (e.g. `"tactics"`, `"general_play"`) |
| `difficulty` | `float` | 0.0–1.0 |
| `format` | `string` | Training format (e.g. `"game"`, `"puzzle"`) |
| `expected_gain` | `float` | Estimated rating gain |

### ⚠ Schema conflict with `POST /curriculum/next`

`POST /curriculum/next` is a distinct endpoint (SECA router) with a **different
response schema**:

```json
{
  "topic":         <string>,
  "difficulty":    <float | string>,
  "exercise_type": <string>,
  "payload":       <object>
}
```

The fields `format` / `exercise_type` and `expected_gain` / `payload` are not
interchangeable. Clients MUST NOT assume the two endpoints return the same shape.

---

## 3. `POST /game/finish`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Route prefix:** `/game`

### Request body

```json
{
  "pgn":        <string>,
  "result":     <"win" | "loss" | "draw">,
  "accuracy":   <float 0..1>,
  "weaknesses": <object: {string: float}>,
  "player_id":  <string | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `pgn` | `string` | Non-empty, ≤ 100 000 chars. **Authoritative trust input** — the server re-analyses this PGN with the engine pool to derive `accuracy` + `weaknesses` server-side (PR #142, `seca/analysis/pgn_accuracy.py`). |
| `result` | `string` | Exactly one of `"win"`, `"loss"`, `"draw"` |
| `accuracy` | `float` | 0.0 ≤ value ≤ 1.0. **Accepted but server-side recompute is authoritative** — the client value is used as a fallback only when the engine pool is unavailable or the PGN can't be parsed (logged as `ACC_FALLBACK` server-side). A modded client cannot inflate the bandit's reward signal by sending `accuracy=1.0`. |
| `weaknesses` | `object` | ≤ 50 keys; values are numeric. Same authority model as `accuracy` — server-side recompute overrides. |
| `player_id` | `string \| null` | If provided, must match authenticated player |

### Response

```json
{
  "status":     "stored",
  "new_rating": <float>,
  "confidence": <float>,
  "learning":   <object>,
  "coach_action": {
    "type":     <string>,
    "weakness": <string | null>,
    "reason":   <string>
  },
  "coach_content": {
    "title":       <string>,
    "description": <string>,
    "payload":     <object>
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | `string` | Always `"stored"` on success |
| `new_rating` | `float` | Updated player rating |
| `confidence` | `float` | Updated player confidence |
| `learning` | `object` | Contains `{"status": <string>}` |
| `coach_action.type` | `string` | One of: `"NONE"`, `"REFLECT"`, `"DRILL"`, `"PUZZLE"`, `"PLAN_UPDATE"` |
| `coach_action.weakness` | `string \| null` | Weakness name when type is `DRILL` or `PLAN_UPDATE` |
| `coach_action.reason` | `string` | Human-readable decision reason |
| `coach_content.title` | `string` | Content title shown to player |
| `coach_content.description` | `string` | Content description |
| `coach_content.payload` | `object` | Type-specific content payload |

---

## 4. `POST /live/move`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>` (route depends on
`get_current_player`, so JWT is required alongside the API key; absent or
invalid Bearer returns 401)

### Request body

```json
{
  "fen":       <string>,
  "uci":       <string>,
  "player_id": <string | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN string; non-empty |
| `uci` | `string` | UCI move (4–5 chars, e.g. `"e2e4"`, `"e7e8q"`) |
| `player_id` | `string \| null` | Optional player identifier |

### Response

```json
{
  "status":             "ok",
  "hint":               <string>,
  "engine_signal":      <object>,
  "move_quality":       <string>,
  "mode":               "LIVE_V1",
  "dynamic_adaptation": <bool>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | `string` | Always `"ok"` on success |
| `hint` | `string` | Human-readable coaching hint; may be empty string `""` |
| `engine_signal` | `object` | Structured evaluation context (see `EngineSignalDto`) |
| `move_quality` | `string` | Quality label: `"good"`, `"inaccuracy"`, `"mistake"`, `"blunder"` |
| `mode` | `string` | Always `"LIVE_V1"` for this endpoint |
| `dynamic_adaptation` | `bool` | Per-player dynamic-adaptation flag from `_dynamic_registry.get_state(player_id).enabled`. Always present in the response. Drives the client-side adaptation indicator only — does not change the engine signal, the validators, or the LLM trust boundary. |

### Notes
- `hint` must be preserved as-is by clients even when empty; clients must not
  substitute `null` for an empty string.
- Tested end-to-end by `LiveMoveApiClientIntegrationTest` (Android) and
  `test_live_move_pipeline.py` (backend); `dynamic_adaptation` contract pinned by
  `test_dynamic_adaptation.py` and the `validate_live_move_response` schema
  validator in `llm/rag/validators/explain_response_schema.py`.

---

## 5. `POST /chat`

**Host:** `server.py`
**Auth:** `X-Api-Key` required

### Request body

```json
{
  "fen":            <string>,
  "messages":       <array of {role, content}>,
  "player_profile": <object | null>,
  "past_mistakes":  <string[] | null>,
  "move_count":     <int | null>,
  "coach_voice":    <string | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN or `"startpos"` |
| `messages` | `array` | ≤ 50 turns; each message content ≤ 2000 chars |
| `player_profile` | `object \| null` | Optional — keys: `skill_estimate`, `common_mistakes`, `strengths` |
| `past_mistakes` | `string[] \| null` | Optional — ≤ 20 items |
| `move_count` | `int \| null` | Optional — 0–10 000; injects "This is move N of the game." into the context block |
| `coach_voice` | `string \| null` | Optional tone setting. Allow-list: `"formal"`, `"conversational"`, `"terse"` (case-insensitive, whitespace-stripped; empty string is coerced to `null`). Unknown values reject the request with 422. Default `null` → server treats as `"conversational"`. Affects tone only; engine truth and validator gates are unchanged. Pinned by `test_chat_coach_voice.py`. |

### Response

```json
{
  "reply":         <string>,
  "engine_signal": <object>,
  "mode":          "CHAT_V1"
}
```

---

## 6. `POST /seca/explain`

**Host:** `llm/server.py` (SECA inference router, prefix `/seca`)
**Auth:** `X-Api-Key` required

### Request body

```json
{
  "fen":       <string>,
  "player_id": <string>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN for the current position |
| `player_id` | `string` | Identifies the player for skill tracking |

### Response

```json
{
  "explanation": <string>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `explanation` | `string` | Coach explanation generated by the SECA pipeline |

### Notes
- Runs the full SECA pipeline: engine analysis → RAG doc retrieval → LLM explanation
  → skill update → telemetry.
- Distinct from `POST /explain` (root-level, `SAFE_V1` mode) which uses a
  deterministic safe-explainer without the SECA pipeline.

---

## 6b. `/coach` — NOT IMPLEMENTED

The `/coach` endpoint does not exist. Coaching decisions are embedded in
the `POST /game/finish` response (`coach_action` + `coach_content` fields).

Any client expecting a standalone `/coach` endpoint will receive HTTP 404.

---

## 7. `GET /game/history`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required

### Response

```json
{
  "games": [
    {
      "id":           <string>,
      "result":       <"win" | "loss" | "draw">,
      "accuracy":     <float 0..1>,
      "created_at":   <string | null>,
      "rating_after": <float | null>
    }
  ]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `games` | `array` | Up to 20 entries, ordered newest-first |
| `id` | `string` | Game event UUID |
| `result` | `string` | One of `"win"`, `"loss"`, `"draw"` |
| `accuracy` | `float` | 0.0–1.0 as submitted via `POST /game/finish` |
| `created_at` | `string \| null` | ISO-8601 datetime string |
| `rating_after` | `float \| null` | Rating after this game; `null` if no rating update was stored |

---

## 8. `POST /auth/change-password`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` required

### Request body

```json
{
  "current_password": <string>,
  "new_password":     <string>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `current_password` | `string` | Must match the stored hash |
| `new_password` | `string` | Minimum 8 characters |

### Response

```json
{ "status": "updated" }
```

| HTTP Status | Meaning |
|-------------|---------|
| 200 | Password updated successfully |
| 400 | `current_password` does not match (`"Current password is incorrect"`) or `new_password` too short |
| 401 | Invalid or expired token |

---

## 9. `POST /game/coach-feedback`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required

### Request body

```json
{
  "session_fen": <string>,
  "is_helpful":  <boolean>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `session_fen` | `string` | FEN of the board position when feedback was given |
| `is_helpful` | `boolean` | `true` = thumbs up, `false` = thumbs down |

### Response

```json
{ "status": "recorded" }
```

Feedback is logged server-side. This endpoint is fire-and-forget; clients
should not block the UI on the result.

---

## 10. `GET /auth/me` / `PATCH /auth/me`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

### `GET /auth/me`

Returns the authenticated player's profile + skill vector.

#### Response

```json
{
  "id":           <string>,
  "email":        <string>,
  "rating":       <float>,
  "confidence":   <float>,
  "skill_vector": { "<skill>": <float>, ... }
}
```

### `PATCH /auth/me`

Partial profile update — used by the Onboarding flow + the Settings
"Skill rating" affordance to forward calibration to the server.

#### Request body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `rating`     | `float \| null` | no | Bounds: `(0, 4000]`. |
| `confidence` | `float \| null` | no | Bounds: `[0.0, 1.0]`. |

At least one field must be non-null (empty body returns 400).

#### Wire shape (Android client)

The JDK's HttpURLConnection rejects PATCH on JDK 17, so the Android
client sends `POST /auth/me` + `X-HTTP-Method-Override: PATCH` —
the server's `http_method_override` middleware promotes it.

#### Response

Same shape as `GET /auth/me` (post-update values).

### `X-Auth-Token` refresh header

**Both endpoints** (and every other authenticated endpoint that
depends on `get_current_player`) include a `X-Auth-Token` response
header with a freshly-minted JWT bound to the same `session_id`.
Active clients rotate their stored token via this header so the JWT
exp can stay tight (24h) without bouncing active users.

Failure paths (401 / 403 / 422 / 500) do NOT emit `X-Auth-Token` —
defends against a hostile client harvesting tokens by probing.

---

## 11. `POST /game/start`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Creates a new in-progress row in the `games` table.  `player_id` is
derived from the JWT; any value in the request body is ignored.

### Request body

```json
{ "player_id": <string>  // legacy field, ignored }
```

### Response

```json
{ "game_id": <string> }   // UUID
```

Pair with `POST /game/finish` (see §3) and `POST /game/{game_id}/checkpoint`
(see §12) to close the lifecycle properly.

---

## 12. `POST /game/{game_id}/checkpoint`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Persists the in-progress board state for cross-device resume.  Called
by the Android client after every move.

### Path params

| Field     | Type     | Notes                                          |
|-----------|----------|------------------------------------------------|
| `game_id` | `string` | Max 64 chars, no control chars.  Returned by `POST /game/start`. |

### Request body

| Field         | Type     | Required | Description |
|---------------|----------|----------|-------------|
| `fen`         | `string` | yes      | Full FEN of the current position.  Max 256 chars. |
| `uci_history` | `string` | no       | Comma-separated UCI moves (e.g. `"e2e4,e7e5"`).  Max 16 384 chars; defaults to `""`. |

### Response

```json
{ "status": "checkpointed" }
```

### Failure modes

| Status | Meaning |
|--------|---------|
| 400    | `game_id` too long or contains control chars; FEN/uci_history invalid |
| 403    | Game belongs to another player |
| 404    | `game_id` doesn't exist |
| 409    | Game is already finished (cannot checkpoint a closed game) |

---

## 13. `GET /game/active`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Returns the player's most-recent unfinished game with a checkpoint —
the cross-device resume backbone.  Called by `HomeActivity` cold-start
when no local SharedPreferences snapshot exists (fresh install /
device swap).

### Response (200)

```json
{
  "game_id":             <string>,
  "current_fen":         <string>,
  "current_uci_history": <string>,
  "last_checkpoint_at":  <string>,   // ISO timestamp
  "started_at":          <string>    // ISO timestamp
}
```

### Failure modes

| Status | Meaning |
|--------|---------|
| 404    | No resumable game (no unfinished game with a checkpoint).  Treated as **absence-of-data, not error** by the Android client — `getActiveGame()` returns `Success(null)`. |

---

## 14. `GET /repertoire`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>`

Backs the AtriumOpenings screen.  Returns the player's saved
opening lines, or a canonical 4-entry default list when nothing is
stored (defaults are NOT persisted on read — GET stays
side-effect-free).

### Response

```json
{
  "openings": [
    {
      "eco":       <string>,    // e.g. "C84"
      "name":      <string>,    // e.g. "Ruy Lopez · Closed"
      "line":      <string>,    // e.g. "1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6"
      "mastery":   <float>,     // 0.0–1.0
      "is_active": <bool>,      // exactly one entry should be true
      "ordinal":   <int>        // display order (lower = first)
    },
    ...
  ]
}
```

### Notes

- Default ECOs (`C84`, `B22`, `D02`, `A04`) mirror the Android client's
  `OpeningsActivity.DEFAULT_REPERTOIRE` exactly.  A drift test
  (`test_repertoire_endpoint.py::test_default_mirrors_android_companion`)
  pins them so first-vs-subsequent visits never show different defaults.
- Edit endpoints (add / delete / set-active / drill-result) are documented
  in §23–§26 below.  All four seed the default repertoire on first write
  so a user editing one of the canonical lines materialises a persistent
  copy on the same call.

---

## 15. `POST /auth/register`

**Host:** `llm/seca/auth/router.py`
**Auth:** none (creates the credential)
**Rate limit:** 5 / minute

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `email` | `string` | Must pass `_validate_email_strict`. Lower-cased server-side. |
| `password` | `string` | ≤ 1000 chars. |

### Response

```json
{
  "access_token": <string>,
  "player_id":    <string>,
  "token_type":   "bearer"
}
```

### Errors

- `400` — registration failed (duplicate email or invalid email format).
- `429` — rate limit (5 / min per client IP).

---

## 16. `POST /auth/login`

**Host:** `llm/seca/auth/router.py`
**Auth:** none (consumes credentials)
**Rate limit:** 10 / minute

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `email` | `string` | See §15. |
| `password` | `string` | ≤ 1000 chars. |
| `device_info` | `string` | Optional, ≤ 200 chars, no control characters. Recorded against the session row for the upcoming device-list UI; not used for auth decisions today. |

### Response

Same shape as §15.

### Errors

- `401` — invalid credentials (uses constant-time compare server-side, pinned by `test_security_new_findings.py::SN_01`).
- `429` — rate limit.

---

## 17. `POST /auth/logout`

**Host:** `llm/seca/auth/router.py`
**Auth:** `Authorization: Bearer <token>` required (raw header parse — `Header(None)` rather than `get_current_player` so a missing token surfaces as 401, not Pydantic 422; see `AUTH_HDR_02` in `test_auth_missing_header.py`).

### Request body

None (token is sufficient).

### Response

```json
{ "status": "logged_out" }
```

### Errors

- `401` — missing or malformed Authorization header, or invalid / expired token.

---

## 18. `POST /curriculum/next`

**Host:** `llm/seca/curriculum/router.py`
**Auth:** `Authorization: Bearer <token>` required

Returns the next curriculum task driven by (a) game-history-derived dominant mistake category and (b) skill-vector fallback. Backs the Android post-game training prompt.

### Request body

**None.** The route signature is `(player=Depends(get_current_player), db=Depends(get_db))` — no body parameter. The Android client currently sends `{"player_id": "<id>"}` (wire-noise, ignored server-side). The body is **not** authenticated against the bearer token; the `player_id` is derived from `get_current_player`, so a spoofed body field has no authority.

### Response

```json
{
  "topic":             <string>,         // e.g. "tactics", "endgame"
  "difficulty":        <float 0..1>,
  "exercise_type":     <string>,         // "puzzle" | "drill" | "game" | "explanation"
  "payload":           <object>,         // type-specific fields
  "recommendations":   [ {"category": <string>, "priority": <int>, "rationale": <string>}, ... ],
  "dominant_category": <string | null>   // from HistoricalAnalysisPipeline
}
```

### Coexistence with `GET /next-training/{player_id}`

§2 (`GET /next-training/{player_id}`) is a **separate** route with a **different** response shape (`topic`/`difficulty`/`format`/`expected_gain`) — pinned as a documented mismatch in `test_api_contract_validation.py::TestNextTrainingSchemaConflict`. Android calls both. Consolidation is a known follow-up; until then, treat the two as independent contracts.

---

## 19. `GET /player/progress`

**Host:** `llm/seca/analytics/router.py` (mounted on `/player`)
**Auth:** `Authorization: Bearer <token>` required

Returns the authenticated player's complete progress snapshot — backs the Progress screen and the Settings rating-display.

### Response

```json
{
  "current": {
    "rating":              <float>,
    "confidence":          <float>,
    "skill_vector":        {"<skill>": <float>, ...},
    "tier":                <"beginner" | "intermediate" | "advanced">,
    "teaching_style":      <"simple" | "intermediate" | "advanced">,
    "opponent_elo":        <int>,
    "explanation_depth":   <float>,    // 3 dp
    "concept_complexity":  <float>     // 3 dp
  },
  "history": [
    {
      "game_id":          <string>,
      "result":           <"win" | "loss" | "draw">,
      "accuracy":         <float>,
      "rating_after":     <float | null>,
      "confidence_after": <float | null>,
      "weaknesses":       <object>,
      "created_at":       <ISO-8601 string | null>
    },
    ...   // up to 20, newest first
  ],
  "analysis": {
    "dominant_category": <string | null>,
    "games_analyzed":    <int>,
    "category_scores":   {"<category>": <float>, ...},  // 4 dp
    "phase_rates":       {"<phase>":    <float>, ...},  // 4 dp
    "recommendations":   [ {"category": <string>, "priority": <int>, "rationale": <string>}, ... ]
  }
}
```

`history` is empty for a fresh player; `analysis` defaults to `{"dominant_category": null, "games_analyzed": 0, "category_scores": {}, "phase_rates": {}, "recommendations": []}` when there's no history to roll up. The `current.world_model` fields are deterministic functions of rating + confidence — see `seca/adaptation/coupling.py`.

---

## 20. `GET /seca/status`

**Host:** `server.py`
**Auth:** **none** (open endpoint, intentional)

Returns the current SECA runtime safety flag. The Android client polls this at cold-start to confirm the freeze guard is active before sending coaching requests. The endpoint runs `verify_runtime_safety(world_model)` per-request, not just the boot-time `SAFE_MODE` constant, so a lazy-loaded forbidden `brain.*` module after startup flips this to `False` on the next call.

### Response

```json
{ "safe_mode": <bool> }
```

`safe_mode` is `true` on the happy path. A `false` value indicates either (a) a forbidden brain module entered the live runtime after startup, or (b) `verify_runtime_safety` raised and the endpoint fell back to the module-level `SAFE_MODE` constant (which would be `false` on a misconfigured deployment that bypassed the lifespan guard).

Pre-PR-6 the response also carried `bandit_enabled` and `version` fields; both were removed as information-disclosure surfaces with no compensating use case. Clients MUST NOT assume any extra fields.

---

## 21. `POST /chat/stream`

**Host:** `server.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 10 / minute

Streaming variant of §5 (`POST /chat`) — same LLM pipeline, same boundary validators, same fallback-to-deterministic on validation failure — emitted as Server-Sent Events. Note: server awaits the full LLM response before iterating chunks ("fake-streaming"); per-word chunks are post-hoc. Real client-visible streaming is a future improvement (see `[[project-chat-stream-fake-streaming]]`).

### Request body

Same shape as `POST /chat`.

### Response

`Content-Type: text/event-stream` with one event per word:

```
data: {"type": "chunk", "text": "<word> "}\n\n
...
data: {"type": "done", "engine_signal": {...}, "mode": "CHAT_V1"}\n\n
```

The final `done` event carries the ESV + mode. Clients should buffer chunks and treat the `done` event as authoritative for engine state.

### Errors

- `401` — auth failure (no `X-Auth-Token` rotation header on failure paths — see §10).
- `429` — rate limit.
- `500` — boundary validation rejected both the LLM reply AND the deterministic fallback (rare; the deterministic builder is constructed to pass every gate). Surfaces in the Android client as the "Coach is offline" fallback string.

### Persistence

Chat history is saved via `seca/chat/repo.save_exchange` **before** the SSE iterator is constructed, so a network failure mid-stream doesn't leave the turn missing from `GET /chat/history`.

---

## 22. `GET /chat/history`

**Host:** `server.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Returns recent chat turns for the authenticated player. Backs `ChatBottomSheet.preloadServerHistory` so a conversation survives process restarts, device swaps, and reinstalls.

### Query parameters

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `limit` | `int` | `HISTORY_DEFAULT_LIMIT` (50) | Bounded to `[1, HISTORY_MAX_LIMIT=200]` server-side. |

### Response

```json
{
  "turns": [
    {
      "id":         <int>,
      "role":       <"user" | "assistant">,
      "content":    <string>,
      "fen":        <string>,
      "mode":       <string>,           // e.g. "CHAT_V1"
      "created_at": <ISO-8601 string | null>
    },
    ...
  ]
}
```

Turns are returned chronologically (oldest first) so the client can `addAll` directly without re-ordering. Cross-player isolation is by `WHERE player_id = ?` in the repo layer; the route is Bearer-only so the player_id is the authenticated one. No client-supplied player filter is accepted.

---

## 23. `POST /repertoire`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Upsert an opening line (by `eco`). Seeds the default repertoire on first write so a fresh user editing a canonical line materialises a persistent copy.

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `eco` | `string` | `^[A-E][0-9]{2}$` (standard) OR `^[A-Z][0-9A-Z]{1,7}$` (user-coined). ≤ 8 chars, no control characters. |
| `name` | `string` | ≤ 200 chars, no control characters. |
| `line` | `string` | ≤ 500 chars, no control characters. |
| `mastery` | `float` | 0.0 ≤ value ≤ 1.0. Default `0.0`. |

### Response

Same shape as §14 (`GET /repertoire`) — full updated list so the client re-renders in one round-trip.

---

## 24. `DELETE /repertoire/{eco}`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Remove an opening from the player's saved list. Path `eco` is validated the same way as §23's body `eco`.

### Response

Same shape as §14 (`GET /repertoire`) — full updated list.

### Errors

- `404` — opening not found. The Android client treats this as "already gone" and refreshes the list either way.

---

## 25. `POST /repertoire/{eco}/active`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Mark `{eco}` as the player's active line. The "exactly one active" invariant is enforced by an atomic two-write transaction in `seca/storage/repo.set_active_opening`.

### Request body

None.

### Response

Same shape as §14 — full updated list (so the client sees the new active flag + every other line's flag flipped off).

### Errors

- `404` — `eco` doesn't exist for this player after seeding (i.e. it's neither a default nor something they've added).

---

## 26. `POST /repertoire/{eco}/drill-result`

**Host:** `llm/seca/repertoire/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 30 / minute

Apply one drill outcome to the named opening's mastery via an exponential-moving-average update:

```
new = clamp(old + _MASTERY_EMA_STEP * (outcome - old), 0.0, 1.0)
```

`_MASTERY_EMA_STEP = 0.2` — five perfect drills move a fresh line from `0` to `~0.67`; one bad drill of a well-mastered line never collapses it below ~80% of the previous value.

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `outcome` | `float` | 0.0 ≤ value ≤ 1.0. Android maps "Nailed it" / "Mostly" / "Forgot it" to `1.0` / `0.6` / `0.2`. |

### Response

Same shape as §14 — full updated list.

### Errors

- `404` — opening not found, or row vanished mid-update (race; rare).

---

## Error responses

All endpoints return standard FastAPI error shapes:

```json
{ "detail": <string | object> }
```

| HTTP Status | Meaning |
|-------------|---------|
| 400 | Validation error (bad input) |
| 401 | Missing or invalid auth |
| 403 | Authenticated but insufficient permission |
| 409 | Conflict (e.g. checkpointing a finished game) |
| 413 | Request body exceeds 512 KB |
| 422 | Pydantic validation failure |
| 429 | Rate limit exceeded |
