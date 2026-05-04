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

## 1. `POST /engine/eval`  /  `GET /engine/eval`

**Host:** `host_app.py`
**Auth:** none

### Request (POST body or GET query params)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `fen` | `string \| null` | no | FEN string or `"startpos"` |
| `moves` | `string[]` | no | UCI move list (alternative to FEN) |
| `movetime_ms` | `int \| null` | no | Alias: `movetime`. Max engine think time (ms) |
| `nodes` | `int \| null` | no | Max engine nodes to search |

### Response

```json
{
  "score":     <int | null>,
  "best_move": <string | null>,
  "source":    <"engine" | "cache" | "book">,
  "_metrics":  <object>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `score` | `int \| null` | Centipawns from White's perspective. Positive = White better. `null` when engine unavailable (fallback path). |
| `best_move` | `string \| null` | Best move in UCI notation (e.g. `"e2e4"`). `null` when no legal moves or engine unavailable. |
| `source` | `string` | One of `"engine"`, `"cache"`, `"book"`. |
| `_metrics` | `object` | Internal diagnostics. Always present. Structure varies by source. |

### Known mismatches / gaps
- `_metrics` has no stable schema contract; shape depends on `source`.
- `score` semantics (centipawns from White) are not enforced by schema validation.

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
| `pgn` | `string` | Non-empty, ≤ 100 000 chars |
| `result` | `string` | Exactly one of `"win"`, `"loss"`, `"draw"` |
| `accuracy` | `float` | 0.0 ≤ value ≤ 1.0 |
| `weaknesses` | `object` | ≤ 50 keys; values are numeric |
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
**Auth:** `X-Api-Key` required

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
  "status":        "ok",
  "hint":          <string>,
  "engine_signal": <object>,
  "move_quality":  <string>,
  "mode":          "LIVE_V1"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | `string` | Always `"ok"` on success |
| `hint` | `string` | Human-readable coaching hint; may be empty string `""` |
| `engine_signal` | `object` | Structured evaluation context (see `EngineSignalDto`) |
| `move_quality` | `string` | Quality label: `"good"`, `"inaccuracy"`, `"mistake"`, `"blunder"` |
| `mode` | `string` | Always `"LIVE_V1"` for this endpoint |

### Notes
- `hint` must be preserved as-is by clients even when empty; clients must not
  substitute `null` for an empty string.
- Tested end-to-end by `LiveMoveApiClientIntegrationTest` (Android) and
  `test_live_move_pipeline.py` (backend).

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
  "move_count":     <int | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN or `"startpos"` |
| `messages` | `array` | ≤ 50 turns; each message content ≤ 2000 chars |
| `player_profile` | `object \| null` | Optional — keys: `skill_estimate`, `common_mistakes`, `strengths` |
| `past_mistakes` | `string[] \| null` | Optional — ≤ 20 items |
| `move_count` | `int \| null` | Optional — 0–10 000; injects "This is move N of the game." into the context block |

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
- POST/DELETE endpoints (add / edit / remove) are not yet implemented;
  the screen still has placeholder buttons that toast "coming soon".

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
