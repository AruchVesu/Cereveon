package ai.chesscoach.app

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed request/response models for the coach backend API.
 *
 * Pure Kotlin — no Android or org.json dependencies; fully JVM-testable.
 * Sprint 4.3.C migrated these onto kotlinx-serialization; the shared
 * [ApiJson] config preserves the snake_case wire format
 * (``@SerialName("player_profile")`` / ``@SerialName("past_mistakes")``
 * / etc.).  ``encodeDefaults = false`` ensures optional fields are
 * absent from the wire payload when they are null, matching the
 * pre-migration ``buildJson`` behaviour.
 */

/**
 * A single message in the conversation history, matching the backend schema.
 *
 * [role]    must be "user" or "assistant".
 * [content] is the message text (backend field name is "content", not "text").
 */
@Serializable
data class ChatMessageDto(val role: String, val content: String)

/**
 * Player context sent with every POST /chat request for personalised coaching.
 *
 * Values are sourced from the most recent [GameFinishResponse]:
 *  - [rating]     Current Glicko-2 skill estimate (backend field: `rating`).
 *  - [confidence] Rating confidence in the range 0.0–1.0 (backend field: `confidence`).
 *
 * Maps to the `player_profile` dict accepted by chat_pipeline.generate_chat_reply().
 */
@Serializable
data class PlayerProfileDto(
    val rating: Float,
    val confidence: Float,
)

/**
 * Request body for POST /chat (and /chat/stream — same wire shape).
 *
 * [fen]           current board position in Forsyth-Edwards Notation.
 * [messages]      conversation history (most-recent last).
 * [playerProfile] optional player context for personalised replies; null
 *                 omits the ``player_profile`` field (``encodeDefaults=false``).
 * [pastMistakes]  optional list of weakness categories from the last game;
 *                 null omits the ``past_mistakes`` field.
 * [moveCount]     optional half-move count for game-phase context during
 *                 mid-game chat; null omits ``move_count``.
 * [coachVoice]    optional coach-voice setting from the user's settings sheet
 *                 (formal / conversational / terse).  Shapes tone, not
 *                 content.  Null omits ``coach_voice``.
 */
@Serializable
data class ChatRequestBody(
    val fen: String,
    val messages: List<ChatMessageDto>,
    @SerialName("player_profile") val playerProfile: PlayerProfileDto? = null,
    @SerialName("past_mistakes") val pastMistakes: List<String>? = null,
    @SerialName("move_count") val moveCount: Int? = null,
    @SerialName("coach_voice") val coachVoice: String? = null,
    // Current server game id, so the server scopes this exchange to its game
    // thread (per-game chat history). Null when no active game → player-global.
    @SerialName("game_id") val gameId: String? = null,
)

/**
 * Request body for POST /game/coach-feedback.  Fire-and-forget thumbs-up /
 * thumbs-down for the latest coaching reply at the given position.
 */
@Serializable
data class CoachFeedbackRequest(
    @SerialName("session_fen") val sessionFen: String,
    @SerialName("is_helpful") val isHelpful: Boolean,
)

/**
 * Centipawn evaluation band returned by the engine for display in the context header.
 * Null fields indicate the server omitted the field.
 */
@Serializable
data class EvaluationDto(
    val band: String? = null,
    val side: String? = null,
)

/**
 * Engine context signal attached to each /chat response.
 * Null fields indicate the server omitted the field.
 */
@Serializable
data class EngineSignalDto(
    val evaluation: EvaluationDto? = null,
    val phase: String? = null,
)

/**
 * Typed response from POST /chat.
 *
 * [reply]        the coaching text to display in the chat UI.
 * [engineSignal] optional engine context for the context header; null when omitted.
 */
@Serializable
data class ChatResponseBody(
    val reply: String = "",
    @SerialName("engine_signal") val engineSignal: EngineSignalDto? = null,
)

/**
 * One persisted chat turn returned by GET /chat/history.
 *
 * The server stores every user message + assistant reply that
 * survived boundary validation (see ``llm/seca/chat/repo.py``).
 * The client seeds [ChatSessionStore] from these on
 * [ChatBottomSheet.onAttach] so a conversation survives process
 * restarts, device swaps, and reinstalls.
 *
 * Roles are ``"user"`` or ``"assistant"`` (matching
 * [ChatMessageDto.role]).  Server-stored ``"system"`` rows
 * (compaction summaries) are NOT yet emitted by the persistence
 * layer but the field is left wide-typed for future expansion.
 */
@Serializable
data class ChatHistoryTurnDto(
    val id: String,
    val role: String,
    val content: String,
    val fen: String? = null,
    val mode: String = "CHAT_V1",
    @SerialName("created_at") val createdAt: String? = null,
)

/**
 * Response body for GET /chat/history?limit=N.
 *
 * Turns are returned in chronological (oldest first) order so the
 * client can `addAll` into its message list without re-sorting.
 * Empty list when the player has no persisted history yet.
 */
@Serializable
data class ChatHistoryResponseBody(
    val turns: List<ChatHistoryTurnDto> = emptyList(),
)

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
 *  - [Abort]       Terminal event when the server could not safely complete the
 *                  stream (validate-before-emit aborted): [reply] is the
 *                  deterministic fallback to render IN PLACE of any partial.
 *  - [StreamError] Server or transport error; [message] describes the failure.
 */
sealed class StreamChunk {
    data class Chunk(val text: String) : StreamChunk()
    data class Done(val engineSignal: EngineSignalDto?, val mode: String) : StreamChunk()
    data class Abort(
        val reply: String,
        val engineSignal: EngineSignalDto?,
        val mode: String,
    ) : StreamChunk()
    data class StreamError(val message: String) : StreamChunk()
}
