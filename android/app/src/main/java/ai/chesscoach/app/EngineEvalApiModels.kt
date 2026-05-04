package ai.chesscoach.app

/**
 * Request/response models for POST /engine/eval (host_app.py).
 *
 * The endpoint requires no authentication — X-Api-Key is not sent.
 * All JSON serialisation/deserialisation is handled in [HttpEngineEvalClient].
 *
 * Schema documented in docs/API_CONTRACTS.md §1.
 */

/**
 * Request body for POST /engine/eval.
 *
 * [fen] Current board position in Forsyth-Edwards Notation, or "startpos".
 */
data class EngineEvalRequest(val fen: String)

/**
 * Response from POST /engine/eval.
 *
 * [score]    Centipawn evaluation from White's perspective.
 *            Positive → White is ahead; negative → Black is ahead.
 *            Null when the engine is unavailable (fallback path).
 * [bestMove] Best move in UCI notation (e.g. "e2e4").
 *            Null when there are no legal moves or engine unavailable.
 * [source]   One of "engine", "cache", or "book".
 */
data class EngineEvalResponse(
    val score: Int?,
    val bestMove: String?,
    val source: String,
)
