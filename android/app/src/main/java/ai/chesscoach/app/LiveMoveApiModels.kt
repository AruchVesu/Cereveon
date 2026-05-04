package ai.chesscoach.app

/**
 * Request/response models for POST /live/move (server.py).
 *
 * The endpoint requires X-Api-Key authentication.
 * All JSON serialisation/deserialisation is handled in [HttpLiveMoveClient].
 *
 * Schema documented in docs/API_CONTRACTS.md §5.
 */

/**
 * Request body for POST /live/move.
 *
 * [fen]       Board position after the move in FEN notation or "startpos".
 * [uci]       The move just played in UCI notation (e.g. "e2e4", "e7e8q").
 * [playerId]  Player identifier; reserved for future profile enrichment.
 */
data class LiveMoveRequest(
    val fen: String,
    val uci: String,
    val playerId: String = "demo",
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
data class LiveMoveResponse(
    val status: String,
    val hint: String,
    val moveQuality: String,
    val mode: String,
    val engineSignal: EngineSignalDto? = null,
)
