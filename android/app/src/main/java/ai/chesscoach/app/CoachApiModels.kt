package ai.chesscoach.app

/**
 * Typed request/response models for the coach backend API.
 *
 * Pure Kotlin — no Android or org.json dependencies; fully JVM-testable.
 * All JSON serialisation/deserialisation is handled in [HttpCoachApiClient].
 */

/**
 * A single message in the conversation history, matching the backend schema.
 *
 * [role]    must be "user" or "assistant".
 * [content] is the message text (backend field name is "content", not "text").
 */
data class ChatMessageDto(val role: String, val content: String)

/**
 * Player context sent with every POST /chat request for personalised coaching.
 *
 * Values are sourced from the most recent [GameFinishResponse]:
 *  - [rating]     Current Glicko-2 skill estimate (backend field: `rating`).
 *  - [confidence] Rating confidence in the range 0.0–1.0 (backend field: `confidence`).
 *
 * Maps to the `player_profile` dict accepted by chat_pipeline.generate_chat_reply().
 * Null values in the dict are omitted by [HttpCoachApiClient.buildJson].
 */
data class PlayerProfileDto(
    val rating: Float,
    val confidence: Float,
)

/**
 * Request body for POST /chat.
 *
 * [fen]           current board position in Forsyth-Edwards Notation.
 * [messages]      conversation history (most-recent last).
 * [playerProfile] optional player context for personalised replies; null omits the field.
 * [pastMistakes]  optional list of weakness categories from the last game; null omits the field.
 */
data class ChatRequestBody(
    val fen: String,
    val messages: List<ChatMessageDto>,
    val playerProfile: PlayerProfileDto? = null,
    val pastMistakes: List<String>? = null,
)

/**
 * Centipawn evaluation band returned by the engine for display in the context header.
 * Null fields indicate the server omitted the field.
 */
data class EvaluationDto(val band: String?, val side: String?)

/**
 * Engine context signal attached to each /chat response.
 * Null fields indicate the server omitted the field.
 */
data class EngineSignalDto(val evaluation: EvaluationDto?, val phase: String?)

/**
 * Typed response from POST /chat.
 *
 * [reply]        the coaching text to display in the chat UI.
 * [engineSignal] optional engine context for the context header; null when omitted.
 */
data class ChatResponseBody(val reply: String, val engineSignal: EngineSignalDto?)

/**
 * Discriminated union for all possible outcomes of a [CoachApiClient] call.
 *
 * Callers should handle all four variants; use `when` with exhaustive branches.
 *
 *  - [Success]      HTTP 200 with a valid parsed body.
 *  - [HttpError]    Server returned a non-200 status code.
 *  - [NetworkError] Transport-level failure (DNS, refused connection, etc.).
 *  - [Timeout]      Connect or read deadline exceeded.
 */
sealed class ApiResult<out T> {
    data class Success<out T>(val data: T) : ApiResult<T>()
    data class HttpError(val code: Int) : ApiResult<Nothing>()
    data class NetworkError(val cause: Throwable) : ApiResult<Nothing>()
    object Timeout : ApiResult<Nothing>()
}

/**
 * Discriminated union for a single Server-Sent Event from POST /chat/stream.
 *
 *  - [Chunk]       A partial text fragment to be appended to the assistant message.
 *  - [Done]        Final event carrying the engine signal and pipeline mode.
 *  - [StreamError] Server or transport error; [message] describes the failure.
 */
sealed class StreamChunk {
    data class Chunk(val text: String) : StreamChunk()
    data class Done(val engineSignal: EngineSignalDto?, val mode: String) : StreamChunk()
    data class StreamError(val message: String) : StreamChunk()
}
