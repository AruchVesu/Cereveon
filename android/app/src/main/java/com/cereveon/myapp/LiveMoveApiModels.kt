package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Request/response models for POST /live/move (server.py).
 *
 * The endpoint requires X-Api-Key authentication.
 * Sprint 4.3.C migrated these off hand-rolled ``org.json.JSONObject``
 * parsing onto kotlinx-serialization — see [HttpLiveMoveClient].
 *
 * Schema documented in docs/API_CONTRACTS.md §5.
 */

/**
 * Request body for POST /live/move.
 *
 * [fen]        Board position after the move in FEN notation or "startpos".
 * [uci]        The move just played in UCI notation (e.g. "e2e4", "e7e8q").
 * [playerId]   Player identifier; reserved for future profile enrichment.
 * [fenBefore]  Board position BEFORE the move.  Lets the server grade move
 *              quality from the eval swing fen_before -> fen (it can't
 *              reconstruct the pre-move position from the post-move FEN — a
 *              capture/en-passant/promotion loses the captured piece).  Null
 *              (the default) is dropped from the wire by ``encodeDefaults=false``
 *              so the server falls back to move_quality "unknown", the
 *              pre-feature behaviour.
 */
@Serializable
data class LiveMoveRequest(
    val fen: String,
    val uci: String,
    @SerialName("player_id") val playerId: String = "demo",
    @SerialName("fen_before") val fenBefore: String? = null,
    // Distinct-game key for the free-tier entitlements admission
    // (API_CONTRACTS.md §4): the server meters LLM-coached GAMES per
    // day, not moves, keyed on this id.  Null (older flows / no server
    // game yet) is dropped from the wire by ``encodeDefaults=false``
    // and the server fails OPEN — the hint stays on the LLM path.
    @SerialName("game_id") val gameId: String? = null,
)

/**
 * Entitlements posture attached to POST /live/move responses
 * (API_CONTRACTS.md §4, additive 2026-07).
 *
 * [plan]       "free" / "pro".
 * [degraded]   True when this hint came from the deterministic coach
 *              because the game is over the plan's daily coached-game
 *              quota — the UI shows its upgrade/limit chip.  Engine
 *              analysis is unaffected; only the hint source changes.
 * [remaining]  Distinct coached games left today; null while metering
 *              is dormant ("not metered", distinct from 0).
 */
@Serializable
data class CoachTierDto(
    val plan: String = "free",
    val degraded: Boolean = false,
    val remaining: Int? = null,
)

/**
 * Response from POST /live/move.
 *
 * [status]       Always "ok" on success.
 * [hint]         Coaching hint referencing engine evaluation, phase, and move quality.
 * [moveQuality]  last_move_quality from the engine signal ("best", "blunder", etc.).
 * [mode]         Always "LIVE_V1" for this pipeline version.
 * [engineSignal] Structured evaluation context from the backend engine signal;
 *                null when absent or unparseable.  Matches [EngineSignalDto] from
 *                the /chat response so the same display logic can be reused.
 */
@Serializable
data class LiveMoveResponse(
    val status: String = "ok",
    val hint: String = "",
    @SerialName("move_quality") val moveQuality: String = "unknown",
    val mode: String = "LIVE_V1",
    @SerialName("engine_signal") val engineSignal: EngineSignalDto? = null,
    // Null when the server pre-dates entitlements (ignoreUnknownKeys
    // covers the reverse direction) — treated as "not metered".
    @SerialName("coach_tier") val coachTier: CoachTierDto? = null,
)
