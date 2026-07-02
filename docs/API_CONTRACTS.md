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

## 2. `GET /next-training/{player_id}` — RETIRED in PR 26

The endpoint was a placeholder implementation with hardcoded "demo
weaknesses" that never advanced past the comment in the source.
Android always called `POST /curriculum/next` first (the SECA-driven
authoritative path); `/next-training` was the fallback that ran when
`/curriculum/next` failed — but the fallback was showing fake-data
recommendations, not a real signal.  Retired in PR 26 (2026-05-15)
alongside the Android-side `getNextTraining` method +
`TrainingRecommendation` DTO.  See `§18 POST /curriculum/next` for
the surviving training-recommendation contract.

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
  },
  "analysis": {
    "dominant_category":  <string | null>,
    "games_analyzed":     <int>,
    "recommendations":    [...]
  },
  "biggest_mistake": null | {
    "fen":           <string>,
    "played_move":   <UCI string>,
    "move_number":   <int>,
    "eval_loss_cp":  <int>,
    "source_ref":    <string>
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
| `biggest_mistake` | `object \| null` | The player's **first** move whose centipawn loss clears `MIN_MISTAKE_LOSS_CP` (150 cp), or `null` when (a) the engine recompute fell back to client values, or (b) no move clears the threshold.  Drives the Android Phase-3 mistake-replay sheet.  Selection policy is "first above threshold" (not "largest loss") so the player learns the originating mistake before its downstream cascade — the wire field name is retained from PR #192's original "biggest loss" picker for backward compatibility with the Android decoder. |
| `biggest_mistake.fen` | `string` | FEN of the position **before** the bad move. |
| `biggest_mistake.played_move` | `string` | UCI of the move the player actually played at that position. |
| `biggest_mistake.move_number` | `int` | 1-indexed Nth player half-move (not Nth ply).  Used in the replay sheet header copy. |
| `biggest_mistake.eval_loss_cp` | `int` | Centipawn loss this move cost the player. Always ≥ 150 when the field is populated. |
| `biggest_mistake.source_ref` | `string` | Opaque identifier to pass back to `POST /training/solve` on a verified-correct replay so dedup works (`event_<event_id>:move_<n>`). |

---

## 4. `POST /live/move`

**Host:** `server.py`
**Auth:** `X-Api-Key` + `Authorization: Bearer <token>` (route depends on
`get_current_player`, so JWT is required alongside the API key; absent or
invalid Bearer returns 401)

### Request body

```json
{
  "fen":        <string>,
  "uci":        <string>,
  "player_id":  <string | null>,
  "fen_before": <string | null>
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `fen` | `string` | Valid FEN string; non-empty. Position **after** the move. |
| `uci` | `string` | UCI move (4–5 chars, e.g. `"e2e4"`, `"e7e8q"`) |
| `player_id` | `string \| null` | Optional player identifier |
| `fen_before` | `string \| null` | Optional — position **before** the move. When present (and `fen_before` + `uci` actually reaches `fen`, an integrity check), the server runs a second Stockfish eval on it and grades move quality from the centipawn swing `fen_before → fen`, surfaced as `engine_signal.last_move_quality` / `move_quality`. The server can't reconstruct the pre-move position from `fen` alone (a capture / en-passant / promotion loses the captured piece), so the client supplies it. Absent/null → `move_quality` stays `"unknown"` (pre-feature behaviour). Validated through the same FEN gate as `fen`. Additive + backward-compatible (no `X-API-Version` bump). |

### Response

```json
{
  "status":             "ok",
  "hint":               <string>,
  "engine_signal":      <object>,
  "move_quality":       <string>,
  "mode":               "LIVE_V1"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | `string` | Always `"ok"` on success |
| `hint` | `string` | Human-readable coaching hint; may be empty string `""` |
| `engine_signal` | `object` | Structured evaluation context (see `EngineSignalDto`) |
| `move_quality` | `string` | Quality label: `"good"`, `"inaccuracy"`, `"mistake"`, `"blunder"` |
| `mode` | `string` | Always `"LIVE_V1"` for this endpoint |

The previous response carried a `dynamic_adaptation` boolean from the
in-process `_dynamic_registry`. That registry + its `/adaptation/mode`
control surface + the related `/move` endpoint were retired in PR 23
(2026-05-15) after the SECA-Android wiring audit confirmed no Android
caller had ever exercised any of them. The `validate_live_move_response`
Pydantic schema was already lenient to the extra field, so the removal
is wire-backward-compatible with any unknown client.

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
  "move_count":     <int | null>,
  "coach_voice":    <string | null>,
  "game_id":        <string | null>,
  "last_move":      <string | null>
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
| `game_id` | `string \| null` | Optional — per-game chat thread key (the client's current `games.id`). When present, the saved exchange is scoped to that game so `GET /chat/history?game_id=…` shows only that game's chat; absent/null keeps it player-global (legacy). ≤ 64 chars; empty → `null`. `player_id` (from the JWT) stays the isolation boundary, so this is an organizational key only. Same field on `POST /chat/stream`. |
| `last_move` | `string \| null` | Optional — the player's most recent move in UCI (`[a-h][1-8][a-h][1-8]` + optional promotion `[qrbnQRBN]`). Lets the coach describe it in plain English ("you advanced your f-pawn") instead of misreading the raw FEN; the server renders it coordinate-free via `describe_move_plain` so the no-notation output rule isn't tripped. Absent/null → no move line. Same field on `POST /chat/stream`. 422 on malformed UCI. |

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
      "game_id":      <string | null>,
      "last_move":    <string | null>,
      "winner_move":  <string | null>,
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
| `id` | `string` | Game event UUID (the `game_events` row id) |
| `game_id` | `string \| null` | Live game id (the `games.id` from `POST /game/start`, equal to `chat_turns.game_id`). Pass to `GET /chat/history?game_id=…` to load this game's coaching chat. `null` for legacy rows, imported (e.g. Lichess) games, and finishes from clients that didn't send a `game_id` — those have no per-game chat thread. |
| `last_move` | `string \| null` | SAN of the final mainline move (e.g. `"Nc6"`, `"Qxh7#"`), derived server-side from the stored PGN, so the history list can preview how each game ended. `null` for moveless or unparseable / legacy PGN. |
| `winner_move` | `string \| null` | SAN of the **winning side's** final mainline move, per the PGN `Result` header (`1-0` = White, `0-1` = Black). Differs from `last_move` when the loser made the last move on the board. `null` for draws, ongoing / unknown results, moveless or unparseable PGN. |
| `result` | `string` | One of `"win"`, `"loss"`, `"draw"` |
| `accuracy` | `float` | 0.0–1.0 as submitted via `POST /game/finish` |
| `created_at` | `string \| null` | ISO-8601 datetime string |
| `rating_after` | `float \| null` | Rating after this game; `null` if no rating update was stored |

---

## 7a. `GET /game/{event_id}/positions`

**Host:** `llm/seca/events/router.py`
**Auth:** `Authorization: Bearer <token>` required

Per-ply board positions for replaying a finished game in the history "review"
screen. Derived server-side from the stored `GameEvent.pgn` (python-chess) —
the client never parses PGN. Keyed by `event_id` (the `id` from
`GET /game/history`), so it works for every game, including legacy rows with no
chat thread.

### Response

```json
{
  "positions": ["<fen>", "…"],
  "moves":     ["<san>", "…"]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `positions` | `array<string>` | N+1 FENs: index 0 is the start, index *i* is the board after ply *i* (`positions` last entry is the final position) |
| `moves` | `array<string>` | N SANs: `moves[i]` produced `positions[i+1]` (e.g. `"e4"`, `"Nf3"`); for move-list labels |

Status codes: `400` (`event_id` over the 64-char cap), `403` (not the owner), `404` (unknown event).

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
  "skill_vector": { "<skill>": <float>, ... },
  "training_xp":  <int>
}
```

`training_xp` is a monotonic counter incremented when the player completes a
training exercise (the seed = replay of an engine-flagged mistake from their
own game; the derivatives = weekly micro-tasks of the same mistake pattern in
new positions).  The Android client renders it as a Level/XP card on the Home
screen; `rating` and `confidence` are still returned but are no longer shown
to the user — they continue to drive adaptive opponent selection internally.
Defaults to `0` for legacy rows.

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

## 16a. `POST /auth/lichess`

**Host:** `llm/seca/auth/router.py`
**Auth:** none (consumes an OAuth authorization code)
**Rate limit:** 10 / minute

"Sign in with Lichess" — OAuth 2.0 authorization-code + PKCE (RFC 7636).
The Android client runs the authorization step in the system browser
(`LichessOAuth.kt` builds the `https://lichess.org/oauth` URL; the
redirect lands on `ai.chesscoach.app://lichess-auth`), then forwards the
one-time `code` + its `code_verifier` here.  The **server** performs the
code exchange and account fetch
(`llm/seca/lichess/client.py::exchange_authorization_code` /
`fetch_account`), so Lichess access tokens never live on the device and a
token minted for a different app cannot be replayed into a sign-in.  The
pinned OAuth identifiers (`client_id = ai.chesscoach.app`, `redirect_uri =
ai.chesscoach.app://lichess-auth`) must byte-match between
`LichessOAuth.kt` and `llm/seca/lichess/client.py`; Lichess accepts
unregistered public clients, so no upstream registration exists to catch a
drift.  No scopes are requested — public identity only.

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `code` | `string` | 1–512 printable-ASCII chars (opaque Lichess authorization code; single-use). |
| `code_verifier` | `string` | RFC 7636 §4.1 shape: 43–128 chars of `[A-Za-z0-9\-._~]`. |
| `device_info` | `string` | Optional, ≤ 200 chars, no control characters — same rules as §16. |

### Response

```json
{
  "access_token":     <string>,
  "player_id":        <string>,
  "token_type":       "bearer",
  "created":          <bool>,     // true when this sign-in created the account
  "lichess_username": <string>    // display-cased handle from /api/account
}
```

Superset of the §15/§16 shape — the Android client deserialises it as
`LoginResponse` (`ignoreUnknownKeys`).

### Semantics

- The player row is keyed on `players.lichess_user_id` (the canonical
  lowercase id from `GET /api/account`, shape-validated fail-closed).
- First sign-in creates the account with a synthetic
  `email = "lichess:<id>"` — outside the reachable email space (`_EMAIL_RE`
  rejects it at §15/§16, so the namespace cannot be squatted;
  `test_auth_lichess.py::LI_10`) — and an unusable random password hash.
- The Lichess access token is revoked (best-effort `DELETE /api/token`)
  immediately after the account fetch — including when the fetch itself
  fails after a successful exchange, so no live token is left dangling.
- Best-effort auto-link: when the player has no `/lichess` link yet, the
  game-import link (§27) + first-link calibration are created from the
  already-fetched account profile.  Link failure (including a cross-player
  409 conflict) never fails the sign-in, and an existing link — even to a
  different handle — is never modified.

### Errors

- `401` — Lichess rejected the grant (invalid / expired / replayed code, or
  PKCE verifier mismatch).  The client must restart the authorization flow.
- `422` — request-shape validation (malformed `code` / `code_verifier`).
- `502` — Lichess upstream error or malformed upstream response.
- `503` — Lichess rate-limited the exchange; retry later.
- `429` — our rate limit (10 / min per client IP).

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

**None.** The route signature is `(player=Depends(get_current_player), db=Depends(get_db))` — no body parameter. The player identity is derived from the bearer token. Pre-PR-27 (2026-05-15) the Android client sent `{"player_id": "<id>"}` here which the server silently dropped; the wire-noise was removed when this section's contract was re-pinned.

### Response

```json
{
  "topic":             <string>,         // e.g. "tactics", "endgame"
  "difficulty":        <"easy" | "medium" | "hard">,
  "exercise_type":     <string>,         // see note — "puzzle"|"opening_line"|"middlegame_plan"|"endgame_drill"|"mixed_training"
  "payload":           <object>,         // type-specific fields
  "recommendations":   [ {"category": <string>, "priority": <int>, "rationale": <string>}, ... ],
  "dominant_category": <string | null>   // from HistoricalAnalysisPipeline
}
```

`difficulty` is the band string emitted by
`CurriculumPolicy.choose_difficulty()` — three discrete tiers, not a
continuous 0..1 fraction.  Earlier revisions of this section advertised
`<float 0..1>` to align with a draft contract; the implementation has
always shipped the band string and the doc was the outlier.  The pin
was caught and corrected 2026-05-25 — the Android client
(`CurriculumRecommendation.difficulty`) is `String` from that release
on, and the deserialiser would otherwise throw
`JsonDecodingException` at every call site.  Bidirectional regression
guards: `llm/tests/test_curriculum_next_contract.py::CURR_DIFFICULTY_VALID_LEVEL`
(server side) + `INT_CURR_DIFFICULTY_PARSED` and `INT_CURR_PROD_SHAPE`
in `GameApiClientCurriculumTest` (client side).

`exercise_type` is the value emitted by
`CurriculumPolicy.choose_exercise_type()`, keyed off `topic`: `puzzle`
(tactics), `opening_line` (`opening` / `opening_principles`), `middlegame_plan`
(`middlegame`), `endgame_drill` (`endgame`), and `mixed_training` — the
defensive default for an unrecognised topic.  Earlier revisions of this
section advertised `"puzzle" | "drill" | "game" | "explanation"`; that was the
vocabulary of the retired `task_selector` module (the orphaned `/next-training`
cluster), never what `/curriculum/next` shipped.  Corrected 2026-06-04
alongside closing the mapping gap where the skill-vector fallback topics
`middlegame` and `opening_principles` degraded to `mixed_training` (and a dead
`time_management` → `blitz_simulation` entry was removed).  The Android client
treats `exercise_type` as an opaque display string
(`MainActivity.formatCurriculumChip` uppercases it), so the expanded value set
needs no coordinated client release.  Regression guard:
`llm/tests/test_curriculum_next_contract.py::TestCurriculumExerciseTypeMapping`.

### History — `GET /next-training/{player_id}` retired in PR 26

§2 used to be a parallel `GET /next-training/{player_id}` route with a different
response shape.  Both endpoints coexisted because Android called
`/curriculum/next` first and fell back to `/next-training` on failure — but
`/next-training`'s "weaknesses" were hardcoded demo data, not a real signal.
PR 26 (2026-05-15) retired the legacy route + the Android fallback path,
leaving `POST /curriculum/next` as the sole training-recommendation
contract.

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
| `game_id` | `string` | _(none)_ | Optional — scope history to one game's thread (the client's current `games.id`). Omitted → player-global history (every turn, all games). Absurd lengths (> 64) are ignored (fall back to player-global) rather than 422-ing the fetch. |

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

Turns are returned chronologically (oldest first) so the client can `addAll` directly without re-ordering. Cross-player isolation is by `WHERE player_id = ?` in the repo layer; the route is Bearer-only so the player_id is the authenticated one. No client-supplied player filter is accepted. `game_id`, when supplied, only adds `AND game_id = ?` WITHIN the authenticated player's rows — an organizational sub-filter, not a security boundary.

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

## 27. `POST /lichess/link`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 10 / minute

Attach a Lichess account to the authenticated player.  Validates the
handle via Lichess `GET /api/user/{username}` and, on first link
(player still at default rating 1200 + confidence 0.5), seeds the
player's rating + confidence from the matching Lichess perf rating
(prefers `rapid`, then `blitz`, then `classical`).

The Lichess account is the only external-platform link supported in
this release.  Imported `GameEvent` rows carry `source='lichess'` and
the Lichess game ID (see §3 "Imported games" and the lazy-re-analysis
note below).

### Request body

| Field | Type | Constraints |
|-------|------|-------------|
| `username` | `string` | 2–30 chars; `[A-Za-z0-9_-]` only.  The Lichess signup-form rule. |

### Response

```json
{
  "platform":          "lichess",
  "external_username": <string>,
  "linked_at":         <ISO-8601 string | null>,
  "calibration": {
    "applied":     <bool>,
    "reason":      <string | absent>,
    "perf":        <"rapid" | "blitz" | "classical" | absent>,
    "rating":      <float | absent>,
    "confidence":  <float | absent>,
    "games_basis": <int | absent>,
    "provisional": <bool | absent>
  }
}
```

- `external_username` is the **canonical lowercase** Lichess id, not
  the user-submitted casing.  Clients should display this verbatim.
- `calibration.applied = true` means the player's rating + confidence
  were updated; `false` means they were preserved (either no eligible
  perf or the player had already moved off defaults via in-app play).
- `reason` is `"player_already_calibrated"` or `"no_eligible_perf"` on
  the `applied=false` paths.

### Errors

- `400` — username failed schema validation, or the Lichess profile
  payload was missing the `id` field.
- `404` — Lichess returned 404 for the username.
- `409` — that Lichess handle is already linked to another ChessCoach
  player.  Detail: `"Lichess account '<id>' is linked to another
  player"`.
- `502` — Lichess returned 5xx, a non-special-cased 4xx, or its body
  failed to parse.
- `503` — Lichess returned 429.  Carries `Retry-After` header when
  Lichess provided one (numeric seconds; non-numeric values are
  dropped silently).

### Trust-boundary note

This endpoint is the only place that mutates `Player.rating` /
`Player.confidence` from Lichess data, and it does so at most once per
player (calibration is gated on default values).  Imported games never
touch the rating model — that invariant is the reason backfilling years
of historical games does not whipsaw the in-app FIDE-style Elo.

---

## 28. `DELETE /lichess/link`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 10 / minute

Detach the player's Lichess link.  Already-imported `game_events` rows
are **retained** as game history; only the `linked_accounts` row is
removed.

### Request body

Empty.

### Response

```json
{ "unlinked": <bool> }
```

- `true` — a link existed and was removed.
- `false` — the player had no Lichess link to begin with (idempotent).

---

## 29. `GET /lichess/status`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** (not rate-limited; cheap read used for client polling.)

Report the player's current Lichess link state and import counters.

### Request

No body, no query params.

### Response — when not linked

```json
{ "linked": false }
```

### Response — when linked

```json
{
  "linked":                true,
  "platform":              "lichess",
  "external_username":     <string>,
  "linked_at":             <ISO-8601 string | null>,
  "last_imported_at":      <ISO-8601 string | null>,
  "imported_game_count":   <int>,
  "active_import_job_id":  <string (UUID) | null>
}
```

- `last_imported_at` is the `createdAt` of the **newest** game seen in
  the most recent successful import slice — NOT the server clock at
  import time.  The next `/lichess/import` uses this value as the
  Lichess `since` parameter, so import is incremental by construction.
- `imported_game_count` counts only `game_events` rows where
  `source='lichess'` for this player; it does NOT include in-app
  games.
- `active_import_job_id` is non-null iff a v2 import job (§31) is in
  flight (`status='queued'` or `'running'`) for this player.  Added
  alongside the v2 async path so the Android client can rejoin a
  progress view after a sheet dismiss / device restart by passing
  the value to §31a.  Old v1 clients ignore unknown fields and are
  unaffected.

---

## 30. `POST /lichess/import`

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 6 / minute

Pull the next slice of games from Lichess for the linked player.
Synchronous: the request returns when the slice is complete (or when
the cap is reached).  No background-job framework yet — repeated calls
walk forward through history via the `last_imported_at` watermark on
`linked_accounts`.

### Request

No body.  Query parameters:

| Param | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_games` | `int` | `50` | `1 ≤ value ≤ 100` | Hard upper bound on games fetched in this call.  The 100 cap exists to bound request latency in the absence of a background-job framework. |
| `rated` | `bool` | `true` | — | Filter to rated games only. |

Server-side, the perf-type filter is hard-coded to
`["blitz", "rapid", "classical"]` and Lichess `evals=false` is pinned
(see "Trust-boundary note" below).

### Response

```json
{
  "inserted":          <int>,
  "skipped_duplicate": <int>,
  "skipped_invalid":   <int>,
  "last_imported_at":  <ISO-8601 string | null>
}
```

- `inserted` — newly stored `game_events` rows.
- `skipped_duplicate` — games whose `(source='lichess',
  external_game_id)` pair was already in the DB.  This will be
  non-zero on partial-retry calls and is part of the dedup contract.
- `skipped_invalid` — games dropped because their PGN was missing,
  oversize (> 100 000 chars), unparseable by `python-chess`, the
  linked user wasn't listed as a player, or the `id` field was
  missing.  Surfaced for observability — these are **not** errors.
- `last_imported_at` is the new value of the watermark after this
  call.  The watermark is only advanced after a clean iteration; a
  mid-stream failure leaves it unchanged so a retry re-scans the same
  window (dedup handles the repeated rows).

### Errors

- `400` — player has no Lichess link (`/lichess/link` first).
- `422` — `max_games` out of range or `rated` not bool.
- `502` — Lichess upstream error (5xx, malformed NDJSON, connection
  failure mid-stream).  Any rows committed before the failure are
  retained.
- `503` — Lichess rate-limited the request; `Retry-After` propagated
  when present.

### Trust-boundary note

Lichess can return its own Stockfish evaluations when the games
endpoint is called with `evals=true`.  Per `docs/ARCHITECTURE.md`,
**only the local engine pool produces trusted engine output**; this
client therefore pins `evals=false` and the import service never
populates `GameEvent.accuracy` / `GameEvent.weaknesses_json` from
Lichess data.  ESV-based coaching for an imported game is produced
lazily by re-analysing the stored PGN with the local Stockfish pool
when (and only when) the user opens that game for review.

### Versioning note

The v1 synchronous contract documented above is preserved for backward
compatibility with already-shipped Android builds that send
`X-API-Version: 1` (or no header — also routed to v1).  New clients
should target **§31 (`POST /lichess/import` v2 async)** — same path,
same query parameters, but the server returns `202 Accepted` with a
`LichessImportJob` payload and the actual Lichess stream runs on a
server-side worker thread.  The v2 path enables a determinate progress
bar on the client and is the only path that survives a sheet dismiss
or device restart cleanly (the job row carries the resumable state).
Server's `API_VERSIONS_SUPPORTED` is `("1", "2")` until the next bump.

---

## 31. `POST /lichess/import` *(v2 async, `X-API-Version: 2`)*

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 6 / minute
**Header gate:** `X-API-Version: 2`

Same path as §30; the server branches on the `X-API-Version` request
header.  When the value is `"2"`, this v2 path runs: the import is
dispatched to a thread-pool worker and the route returns 202
immediately with the freshly-created (or coalesced) job row.

The actual Lichess NDJSON stream and per-game commits happen on the
worker thread; the client polls §31a to follow progress.

### Request

Identical to §30:

| Param | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_games` | `int` | `50` | `1 ≤ value ≤ 100` | Hard upper bound on games fetched in this call.  Used as the progress bar denominator client-side ("Imported X of up to Y"). |
| `rated` | `bool` | `true` | — | Filter to rated games only. |

Plus the header gate: requests without `X-API-Version: 2` fall through
to §30's v1 path.

### Response

```json
{
  "job_id":            <string (UUID)>,
  "status":            <"queued" | "running" | "succeeded" | "failed">,
  "inserted":          <int>,
  "skipped_duplicate": <int>,
  "skipped_invalid":   <int>,
  "target_max_games":  <int>,
  "last_imported_at_ms": <int (Unix ms) | null>,
  "error_message":     <string | null>,
  "created_at":        <ISO-8601 string>,
  "updated_at":        <ISO-8601 string>
}
```

HTTP status: **202 Accepted**.

- `job_id` — server-issued UUID; pass to §31a to poll progress.
- `status` — `queued` when the row was freshly inserted by this call
  (worker has not picked it up yet); any other value means a
  concurrent caller's job was coalesced into this response.
- `inserted` / `skipped_*` — counters at the moment the row was read
  (`queued` rows are always 0; coalesced rows reflect the worker's
  progress).
- `target_max_games` — pinned at row creation; the client's progress
  bar denominator.  Subsequent coalesced calls do NOT update this
  value (so a second call with a different `max_games` returns the
  ORIGINAL target).
- `last_imported_at_ms` — Unix milliseconds of the newest game seen
  during this run.  Promoted to the canonical
  `LinkedAccount.last_imported_at` (ISO-8601) only on clean
  `succeeded`.

### Coalescing

Concurrent calls for the same player return the same `job_id`.  The
per-player lock in `llm.seca.lichess.get_player_import_lock` is the
primary guard; on Postgres the partial unique index
`ix_lichess_import_jobs_one_active_per_player` is the defense in depth.

### Errors

- `400` — player has no Lichess link (`/lichess/link` first).
- `422` — `max_games` out of range or `rated` not bool.
- `502` — service-layer crash before the worker dispatch.  (Worker
  failures DURING the stream are recorded on the job row's
  `error_message` / `status='failed'`, NOT surfaced through the POST.)

### Trust-boundary note

Same as §30: Lichess `evals=false` is pinned; the import service
never populates `GameEvent.accuracy` / `weaknesses_json` from Lichess
data.

---

## 31a. `GET /lichess/import/job/{job_id}` *(v2 async)*

**Host:** `llm/seca/lichess/router.py`
**Auth:** `Authorization: Bearer <token>` required
**Rate limit:** 120 / minute (60/min headroom over a 2s steady-state poll)

Poll the state of a v2 import job.  Owner-scoped: returns 404 when
the job does not exist OR when it belongs to another player (we do
not differentiate, to avoid leaking the existence of other players'
jobs).

### Request

Path parameter: `job_id` (UUID returned by §31).  No body, no query.

### Response

Same shape as §31's body (200 OK).  Field semantics identical:

```json
{
  "job_id":            <string>,
  "status":            <"queued" | "running" | "succeeded" | "failed">,
  "inserted":          <int>,
  "skipped_duplicate": <int>,
  "skipped_invalid":   <int>,
  "target_max_games":  <int>,
  "last_imported_at_ms": <int | null>,
  "error_message":     <string | null>,
  "created_at":        <ISO-8601 string>,
  "updated_at":        <ISO-8601 string>
}
```

Polling cadence: 2s is the Android client's default and the basis for
the `120/minute` rate limit (steady-state ~30/min + retry headroom).
Stop polling once `status` is `succeeded` or `failed` (terminal); the
field set is otherwise stable so a poll that observes terminal counts
can render the final summary directly.

### Errors

- `404` — job not found OR not owned by current player.

### Cancellation

There is no explicit cancel endpoint.  The two cancellation paths are:

- `DELETE /lichess/link` (§28) — cancels any active jobs for the
  player with `error_message: "link removed during import"` before
  removing the link row.  The worker observes the status change via
  its per-game refresh and exits without advancing the watermark.
- Server restart — the startup janitor
  (`cleanup_stale_import_jobs_on_startup`) sweeps any non-terminal
  rows to `failed` with `error_message: "abandoned by server
  restart"`.

---

## 32. `POST /training/solve`

**Host:** `llm/seca/training/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Credits one verified-solve event to the authenticated player and
bumps `Player.training_xp` by `XP_PER_SOLVE` (10 at Phase 2).  The
endpoint trusts the caller's claim that a solve actually happened —
engine verification is the *caller*'s responsibility (Phase 3 will
run a move-vs-engine-best check on the client + a server-side
double-check before posting here).

### Request body

| Field         | Type             | Required | Description |
|---------------|------------------|----------|-------------|
| `source_type` | `string`         | yes      | One of `"mistake_replay"`, `"weekly_microtask"`, `"standard_puzzle"`. |
| `source_ref`  | `string \| null` | no       | Stable identifier for the solved item (e.g. `"game_<id>:move_<n>"`, a puzzle id, a digest row id).  Bounded at 200 chars; empty / whitespace-only strings are normalised to `null`. |

### Response (200)

```json
{
  "xp_awarded":   <int>,
  "training_xp":  <int>,
  "completed_at": "<ISO-8601>"
}
```

* `xp_awarded` — XP credited for THIS request.  Equals `XP_PER_SOLVE`
  for a new completion; equals `0` when the call deduped against an
  existing row (see *Idempotency* below).  Lets the client tell the
  difference between "you earned XP" and "we already had this one"
  without rendering two response shapes.
* `training_xp` — new running total on the player row.  Lets the
  client update its `PREF_TRAINING_XP` cache + Home Level/XP kicker
  without a separate `/auth/me` round trip.
* `completed_at` — ISO-8601 timestamp.  For a brand-new completion
  this is the row that was just inserted; for a dedup hit this is the
  *historical* row's timestamp.

### Idempotency

`(player_id, source_type, source_ref)` is unique at the database
level when `source_ref` is non-null: a retry of the same logical solve
returns the original row's `completed_at` with `xp_awarded=0` so the
counter doesn't double-bump.  Rows with `source_ref=null` are NOT
deduped (Postgres `NULL`-distinct semantics in the unique index;
intent: catch-all completions where the caller doesn't yet have a
stable identifier).  Phase-3 callers should always supply a stable
ref when dedup matters.

The endpoint handles the unique-constraint race window (two
concurrent requests both pass the pre-check, one commits first, the
other hits the index) by rolling back, re-fetching the existing row,
and returning the dedup response — same observable behaviour as the
pre-check path.

### Rate limit

`60/minute` per client (shared slowapi limiter).  Tuned to permit
normal solve-burst patterns (a weekly digest's 3 micro-tasks finished
back-to-back) without permitting scripted XP farming.

### Errors

| Status | Cause |
|--------|-------|
| `400`  | `source_type` not in the allowed set; `source_ref` exceeds 200 chars. |
| `401`  | Missing or invalid `Authorization` header. |
| `429`  | Rate limit exceeded. |
| `500`  | Unique-constraint race that left no committed row (should be unreachable; logged as `IntegrityError on /training/solve but no row found`). |

---

## 33. `POST /training/verify-replay`

**Host:** `llm/seca/mistakes/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Verify a single mistake-replay attempt against the engine.  Trust
anchor for the Phase 3 XP-credit path: the Android replay sheet
calls this BEFORE calling `POST /training/solve`, so an unverified
move never moves the counter.

### Request body

| Field      | Type     | Required | Description |
|------------|----------|----------|-------------|
| `fen`      | `string` | yes      | Position the player was looking at when they erred. Non-empty, ≤ 200 chars.  Typically the value of `biggest_mistake.fen` from a recent `/game/finish` response. |
| `move_uci` | `string` | yes      | Move the player is proposing as a fix, in UCI notation (e.g. `e2e4`, `e7e8q`). Non-empty, ≤ 8 chars. |

### Response (200)

```json
{
  "is_correct":      <bool>,
  "engine_best_uci": <string>,
  "eval_loss_cp":    <int>
}
```

* `is_correct` — `true` when the user's move gives up at most
  `VERIFY_THRESHOLD_CP` centipawns (30) vs the engine's best move
  in that position.  `false` means "engine ran and says no" —
  the replay UI shows "Not quite, try again" and the user retries.
* `engine_best_uci` — the move Stockfish prefers.  Surfaced even on
  a correct attempt so the UI can offer a "Here's what the engine
  plays" peek without a second round-trip.  Empty string when the
  engine returned no move (edge case; should not happen on a legal
  position).
* `eval_loss_cp` — signed centipawn delta from the player's POV.
  Positive when the user's move is worse than the engine's; can be
  slightly negative on engine search noise (still counts as
  `is_correct=true` since the threshold check is one-sided).

### Rate limit

`60/minute` per client (shared slowapi limiter).  Tuned to permit
normal puzzle-burst patterns while preventing scripted oracle
queries against the engine.

### Errors

| Status | Cause |
|--------|-------|
| `400`  | `fen` cannot be parsed; `move_uci` not legal in the given position; either string exceeds its length cap. |
| `401`  | Missing or invalid `Authorization` header. |
| `429`  | Rate limit exceeded. |
| `503`  | Engine pool unavailable (boot-time failure or queue timeout). Client can show a soft retry. |

---

## 34. `GET /coach/plan/today`

**Host:** `llm/seca/coach/study_plan/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Return the player's most recent active per-mistake study plan + the
puzzle currently due, or `null` when no active plan exists.

A study plan is generated as a side-effect of `POST /game/finish` when
that endpoint identified a `biggest_mistake` (§3) — a background task
on the server writes a 3-puzzle, self-paced **sequence** keyed to the
one originating mistake.  Day 0 is the exact mistake position; days 3
and 7 are theme-matched library variants.  Pacing is sequential, not
calendar-gated: all three are available immediately and the next
unlocks the instant the previous one is solved (the `day_offset`
values 0 / 3 / 7 are sequence labels, not day counts).

Phase 3 (live): the endpoint serves the persisted plan with
LLM-generated `theme` + `verdict` (phase 2) AND with day-3 / day-7
puzzles replaced by theme-matched library variants from the curated
YAML corpus.  Day-0 is always the player's original mistake position.
The Android `TodaysDrillCard` is still pending (phase 4) so no
client polls this endpoint in production today.

The `theme` field is one of the following tags:

```
king_safety
fork
pin
back_rank
hung_piece
queen_safety
tempo
opening_principles
endgame_technique
generic
```

`"generic"` is both an explicit theme (used by the LLM when none of
the named themes fit) AND the fallback value when the LLM path failed
— clients should not treat the two cases differently.  An empty
`verdict` is the only signal that the LLM did not produce a usable
output.

### Request

No body.  Reads the authenticated player from the bearer token.

### Response (200, plan exists)

```json
{
  "plan_id":         <string>,
  "theme":           <string>,
  "verdict":         <string>,
  "anchor_category": <string | null>,
  "status":          <string>,
  "total_days":      3,
  "today_puzzle": {
    "day_offset":         <int>,
    "fen":                <string>,
    "expected_move_uci":  <string>,
    "source_type":        <string>,
    "due_at":             <string>
  } | null,
  "days": [
    {
      "day_offset":  <int>,
      "due_at":      <string>,
      "completed":   <bool>,
      "is_due":      <bool>,
      "source_type": <string>
    }
  ]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `plan_id` | `string` | UUID of the `mistake_study_plans` row.  Stable for the life of the plan.  Pass it (with a `day_offset`) to `POST /coach/plan/puzzle/complete` (§35) when a day is solved. |
| `theme` | `string` | Theme tag of the mistake, from a fixed vocabulary (see below).  Populated by a single-shot LLM call run in the background after `/game/finish`.  Falls back to `"generic"` when the LLM was unreachable, returned out-of-vocabulary, or its output failed the Mode-2 validators on both retries. |
| `verdict` | `string` | LLM-written ≤ 60-word retrospective on the originating mistake.  Mode-2-validator-clean: no specific moves (no algebraic notation, no UCI), no engine mentions, no advisory phrasing.  Empty string when the LLM path failed unrecoverably; Android `TodaysDrillCard` hides the coach-note line in that case. |
| `anchor_category` | `string \| null` | The player's aggregate dominant weakness the week is built around — one of `opening_preparation` / `tactical_vision` / `positional_play` / `endgame_technique` (from `HistoricalAnalysisPipeline` over recent games at `/game/finish`).  The day-3 / day-7 practice puzzles are drawn from this category's theme set; the overview renders it as the week's focus ("This week: Tactics").  `null` for legacy plans and players with too little history to surface a dominant category — those fall back to the day-0 mistake's own `theme` for puzzle selection.  Distinct from `theme`, which describes the day-0 mistake's own motif. |
| `status` | `string` | Plan lifecycle: `"active"` while the week is in progress, `"completed"` once every day is solved.  `GET` only ever returns `"active"` plans (a completed plan returns `null`); `POST /coach/plan/puzzle/complete` (§35) returns the freshly-`"completed"` plan so the client can show the week-complete state. |
| `total_days` | `int` | Number of puzzles in the plan.  Always `3` in phase 1; surfaced as a field so the UI can render "Day N of M" without hard-coding. |
| `today_puzzle` | `object \| null` | The FIRST incomplete day — the next one to solve (sequential pacing).  `null` only when every day is solved (the plan is about to flip to `completed`). |
| `today_puzzle.day_offset` | `int` | One of `0`, `3`, `7`.  Maps to "Day 1 / 3", "Day 2 / 3", "Day 3 / 3" via a static client-side label. |
| `today_puzzle.fen` | `string` | Position the puzzle drops the user into. |
| `today_puzzle.expected_move_uci` | `string` | The engine's preferred move at that FEN (UCI).  For day-0 puzzles this is the player's ORIGINAL bad move — the puzzle asks the user to find a stronger alternative.  For library variants this is the puzzle's expected solution. |
| `today_puzzle.source_type` | `string` | `"original"` for day-0 (the player's actual mistake) and any day whose library lookup didn't find a match; `"library"` for day-3 / day-7 puzzles served from the curated YAML corpus (`llm/seca/coach/study_plan/library/`).  Lets the UI title the puzzle accordingly ("Replay your mistake" vs "Practice: <theme>"). |
| `today_puzzle.due_at` | `string` | ISO-8601 UTC creation timestamp.  Record only — pacing is sequential, not calendar-gated (see `days[].is_due`). |
| `days` | `array` | The full week schedule, ordered by `day_offset` (always `total_days` entries).  Powers the week-overview screen — each entry is `completed` (done), `is_due` (the one to do now), or neither (a later day, locked until the earlier days are solved). |
| `days[].day_offset` | `int` | One of `0`, `3`, `7`. |
| `days[].due_at` | `string` | ISO-8601 UTC creation timestamp (record only; unlock is sequential — see `is_due`). |
| `days[].completed` | `bool` | `true` once the day's puzzle has been solved. |
| `days[].is_due` | `bool` | `true` for the FIRST incomplete day — the one to do now.  Sequential pacing: each day unlocks the instant the previous one is solved (no calendar wait), so exactly one day is `is_due` at a time and it equals `today_puzzle`. |
| `days[].source_type` | `string` | `"original"` or `"library"`, same meaning as `today_puzzle.source_type`. |

### Response (200, no active plan)

```json
null
```

The endpoint returns HTTP 200 with a JSON `null` body when the player
has no active study plan — either no qualifying game has landed yet,
or every plan is completed.  The Android client hides the
`TodaysDrillCard` in this case.

### Puzzle completion + XP credit

On a solved day the client runs three calls, in order:

1. `POST /training/verify-replay` (§33) — engine-truth gate on the move.
2. `POST /training/solve` (§32) — credits XP, with
   `source_type = "mistake_replay"` and
   `source_ref = "plan_<plan_id>:day_<day_offset>"` so the
   `(player, source_type, source_ref)` dedup triple keeps each
   individual puzzle credit-once.
3. `POST /coach/plan/puzzle/complete` (§35) — advances the study plan
   (marks the day done; flips the plan to `completed` when all days
   are solved).

Steps 2 and 3 are distinct on purpose: XP is a global counter, plan
progress is per-plan schedule state.  Step 1 is the trust anchor;
steps 2-3 record the personal, idempotent outcome.

### Rate limit

`60/minute` per client (shared slowapi limiter).  Loose because the
endpoint is a single point read; the client is expected to poll on
each home-screen open.

### Errors

| Status | Cause |
|--------|-------|
| `401`  | Missing or invalid `Authorization` header. |
| `429`  | Rate limit exceeded. |

No 4xx beyond auth — a missing plan returns 200 with `null` body, not
404, because the absence is a normal product state.

---

## 35. `POST /coach/plan/puzzle/complete`

**Host:** `llm/seca/coach/study_plan/router.py`
**Auth:** `Authorization: Bearer <token>` (required)

Mark one day's puzzle in a study plan as solved and advance the plan.
This closes the loop the phase-1 scaffold left open: nothing previously
wrote `MistakeStudyPuzzle.completed_at`, so `GET /coach/plan/today` (§34)
re-served day 0 forever and plans never reached `completed`.  The client
calls this as step 3 of the completion flow (see §34 → "Puzzle
completion + XP credit").

### Trust posture

Records plan PROGRESS, not engine truth.  The engine-truth gate already
happened on `POST /training/verify-replay` (§33), which the client runs
first.  Plan progress carries no cross-user value, so — like
`POST /training/solve` (§32) — the endpoint trusts the caller's
assertion.  It is **idempotent** (re-completing a day is a no-op that
returns 200) and **ownership-scoped** (a plan not owned by the
authenticated player is indistinguishable from a missing one → 404).

### Request

```json
{
  "plan_id":    <string>,
  "day_offset": <int>
}
```

| Field | Type | Notes |
|-------|------|-------|
| `plan_id` | `string` | UUID from the `plan_id` field of `GET /coach/plan/today` (§34). |
| `day_offset` | `int` | The day being completed — one of `0`, `3`, `7` (from `today_puzzle.day_offset`). |

### Response (200)

The full plan, in the **same shape as `GET /coach/plan/today` (§34)**
(`plan_id`, `theme`, `verdict`, `anchor_category`, `status`,
`total_days`, `today_puzzle`, `days`).  Unlike `GET`, this returns the
plan even when the completion
flipped `status` to `"completed"`, so the client can render the next due
puzzle — or the week-complete state — without a second round-trip.

### Rate limit

`60/minute` per client (shared slowapi limiter).

### Errors

| Status | Cause |
|--------|-------|
| `401`  | Missing or invalid `Authorization` header. |
| `404`  | No plan with that `plan_id` owned by the player, or the plan has no puzzle at `day_offset`. |
| `422`  | Body validation failed (missing `plan_id` / `day_offset`, wrong types). |
| `429`  | Rate limit exceeded. |

---

## Error responses

The API emits **two distinct error-body shapes** that any client (the
Android app, server-to-server callers, future SDKs) must handle.  The
duality is historical — the body-size middleware and slowapi
rate-limit handler predate the API_CONTRACTS work and use a different
key from the FastAPI standard.  Until they're consolidated (a
coordinated Android release is needed), both shapes are part of the
contract.

### Shape A — FastAPI / Pydantic standard

```json
{ "detail": <string | object | array> }
```

- `detail` is a **string** for most `raise HTTPException(...)` paths
  (e.g., `{"detail": "invalid FEN"}`).
- `detail` is an **array of `{loc, msg, type}` records** for Pydantic
  422 failures — one entry per failed field.  Example:

  ```json
  { "detail": [{"loc": ["body", "fen"], "msg": "invalid FEN", "type": "value_error"}] }
  ```

- `detail` is a **string** with a structured one-line message for
  API-version mismatches: `{"detail": "X-API-Version mismatch: client
  sent 'X', server supports [Y] (current: 'Z'). Update the client to a
  supported version."}`.

### Shape B — Custom-middleware shape

```json
{ "error": <string> }
```

Used only by three surfaces, all of which run as middleware or as a
slowapi exception handler — i.e., never via `raise HTTPException(...)`:

- `_LimitBodySize` middleware (`llm/server.py`) — 411 / 413 / 400 for
  Content-Length-related rejection.
- `rate_limit_handler` (`llm/server.py`) — 429 for slowapi rate-limit
  exceedance.

### Status-code → shape mapping

| HTTP | Shape | Body example | Trigger |
|------|-------|--------------|---------|
| 400 | A | `{"detail": "invalid FEN"}` | `raise HTTPException(400)` paths: bad FEN / game_id / eco / text-field constraints. |
| 400 | A | `{"detail": "X-API-Version mismatch: …"}` | `api_version_gate` middleware: client `X-API-Version` not in `API_VERSIONS_SUPPORTED`. |
| 400 | B | `{"error": "Invalid Content-Length"}` | `_LimitBodySize` middleware: Content-Length header doesn't parse as int. |
| 401 | A | `{"detail": "Invalid or expired token"}` / `{"detail": "Missing token"}` / `{"detail": "Invalid credentials"}` | `get_current_player` / `logout` / `login` failures; raw `Authorization` header parse on `/auth/logout`. |
| 403 | A | `{"detail": "not your game"}` | Authenticated player operating on another player's resource (game lifecycle). |
| 404 | A | `{"detail": "no active game"}` / `{"detail": "game not found"}` / `{"detail": "opening not found"}` | Resource-not-found across `/game/*`, `/repertoire/*`. |
| 409 | A | `{"detail": "game already finished"}` | Game-lifecycle conflict (checkpointing a finished game). |
| 411 | B | `{"error": "Content-Length header required"}` | `_LimitBodySize` middleware: POST/PUT/PATCH without `Content-Length`. |
| 413 | B | `{"error": "Request body too large"}` | `_LimitBodySize` middleware: Request body exceeds 512 KB (the `_MAX_BODY_BYTES` cap; applies on every endpoint). |
| 422 | A | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}, …]}` | Pydantic validation failure (FastAPI default). |
| 429 | B | `{"error": "Too many requests"}` | `rate_limit_handler` (slowapi). |
| 500 | A | `{"detail": "Internal Server Error"}` / `{"detail": "Server misconfiguration"}` | Unhandled server error (FastAPI default).  See "Errors that DON'T propagate" below for the Mode-2 pipeline cases that look like 500s but never reach Android. |

### Client parsing recipe

For both shapes the message lives behind exactly one of two keys.
Android's HTTP layer should try `body["detail"]` first; on absence,
fall back to `body["error"]`; on absence of both, surface the HTTP
status code itself.  Pydantic 422's array form is the only case where
`detail` is structured rather than scalar — clients that want
field-level error reporting can iterate when `isinstance(detail, list)`.

### Errors that DON'T propagate to Android

These exception classes exist on the server and would surface as 500
if not handled, but the live request paths catch and recover before
the bytes leave the route:

- **`ExplainSchemaError`** (`llm/rag/validators/explain_response_schema.py`) —
  raised by `validate_chat_response` / `validate_live_move_response`
  when the LLM pipeline's output drifts from the boundary schema.  The
  `/chat`, `/chat/stream`, `/live/move` handlers catch it and re-run
  the pipeline with `force_deterministic=True`, which is constructed
  to pass every gate by construction.  Net effect on Android: the
  response is a deterministic-fallback string in the normal `200`
  shape, not a 500.  Closes the cascading-401 lockout pinned by
  `[[project-token-rotation-post-2xx]]`.
- **`OutputFirewallError`** (`llm/rag/safety/output_firewall.py`) —
  raised by `check_output` for PII / identity / prompt-leak / harmful
  patterns in LLM output.  Caught inside `chat_pipeline._build_chat_llm`
  and `rag/deploy/embedded.py`; the offending reply is replaced with
  `"I cannot process this request."` before the route returns.

### Headers on error responses

All error responses (4xx and 5xx) carry the same security headers as
successful responses (CSP, HSTS, X-Frame-Options, etc., from
`add_security_headers` middleware).  Additionally:

- **`X-API-Version`** — always present (set by `api_version_gate` /
  `rate_limit_handler` for the 400-on-mismatch and 429 paths
  explicitly; via the standard response pipeline elsewhere).
- **`X-API-Versions-Supported`** — same as the success response.
- **`X-Auth-Token`** rotation — **NOT** emitted on failure paths
  (401 / 403 / 422 / 5xx).  Defends against a hostile client harvesting
  tokens by probing.  See `commit_pending_auth_rotation` middleware
  rationale in §10 of this document.
