package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/**
 * Shared HTTP client interface for all coach backend endpoints.
 *
 * Each call returns an [ApiResult]; callers never see raw exceptions.
 * Implementations are safe to call from any coroutine context — I/O
 * dispatch is handled internally.
 */
interface CoachApiClient {

    /**
     * Send the current position, conversation history, and optional player context
     * to POST /chat.
     *
     * @param fen           Board position in FEN notation.
     * @param messages      Conversation history (most-recent last).
     * @param playerProfile Optional player context (rating, confidence) for personalised
     *                      coaching; omitted from the request when null.
     * @param pastMistakes  Optional list of weakness categories from the last game; omitted
     *                      from the request when null.
     * @param moveCount     Number of half-moves played so far; gives the backend context
     *                      about game phase during mid-game chat (null omits the field).
     * @return              [ApiResult.Success] on HTTP 200 with a valid body;
     *                      [ApiResult.HttpError] on non-200; [ApiResult.Timeout]
     *                      on deadline exceeded; [ApiResult.NetworkError] otherwise.
     */
    suspend fun chat(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto? = null,
        pastMistakes: List<String>? = null,
        moveCount: Int? = null,
        /**
         * Coach voice setting from the user's Settings sheet
         * (formal / conversational / terse).  Shapes the LLM's tone
         * but never its content.  Null → server uses its default
         * Mode-2 tone.
         */
        coachVoice: String? = null,
    ): ApiResult<ChatResponseBody>

    /**
     * Stream the coaching reply for the current position as Server-Sent Events
     * from POST /chat/stream.
     *
     * Emits [StreamChunk.Chunk] for each text fragment, a single
     * [StreamChunk.Done] when the server closes the stream, or
     * [StreamChunk.StreamError] on transport or HTTP failure.
     *
     * The default implementation delegates to [chat] and wraps the complete
     * reply in a single Chunk + Done, so existing test fakes need not override
     * this method.
     */
    fun chatStream(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto? = null,
        pastMistakes: List<String>? = null,
        moveCount: Int? = null,
        coachVoice: String? = null,
        // Per-game chat thread key. Only the real streaming override
        // (HttpCoachApiClient) forwards it; this non-stream fallback drops it.
        gameId: String? = null,
    ): Flow<StreamChunk> = flow {
        when (val result = chat(fen, messages, playerProfile, pastMistakes, moveCount, coachVoice)) {
            is ApiResult.Success -> {
                emit(StreamChunk.Chunk(result.data.reply))
                emit(StreamChunk.Done(result.data.engineSignal, "CHAT_V1"))
            }
            is ApiResult.HttpError -> emit(StreamChunk.StreamError("HTTP ${result.code}"))
            is ApiResult.NetworkError -> emit(StreamChunk.StreamError("Network error"))
            ApiResult.Timeout -> emit(StreamChunk.StreamError("Timeout"))
        }
    }

    /**
     * POST /game/coach-feedback.
     *
     * Records whether the coaching reply for a given position was helpful.
     * Fire-and-forget: callers should not block the UI on this result.
     * Returns [ApiResult.HttpError(501)] by default so test fakes don't need
     * to override this method.
     */
    suspend fun submitFeedback(
        fen: String,
        isHelpful: Boolean,
        token: String?,
    ): ApiResult<Unit> = ApiResult.HttpError(501)

    /**
     * GET /chat/history — load the most recent persisted chat turns for
     * the authenticated player.
     *
     * The Android client calls this on [ChatBottomSheet.onAttach] so a
     * conversation survives process restarts, device swaps, and
     * reinstalls — the server is the source of truth for chat
     * history; the local [ChatSessionStore] is a UI cache seeded from
     * the server response.
     *
     * Server returns turns in chronological order (oldest first), so
     * the caller can iterate the list and `addAll` directly into the
     * adapter without re-sorting.  The server caps [limit] at
     * ``HISTORY_MAX_LIMIT`` (200) regardless of the requested value;
     * a value > the cap returns the cap.  Default 50 matches the
     * in-memory ``ChatSessionStore`` capacity so a fresh seed never
     * over-fills the cache.
     *
     * Returns [ApiResult.HttpError(501)] by default so test fakes
     * don't need to override this method.
     */
    suspend fun getHistory(
        limit: Int = 50,
        gameId: String? = null,
    ): ApiResult<ChatHistoryResponseBody> = ApiResult.HttpError(501)
}

/**
 * Production implementation of [CoachApiClient] backed by [HttpURLConnection].
 *
 * All I/O is dispatched to [Dispatchers.IO] — the caller needs no special
 * dispatcher. Constructed once and shared; the instance is thread-safe because
 * each [chat] call opens its own connection.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash
 *                         (e.g. "http://10.0.2.2:8000").
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 * @param tokenProvider    Optional supplier of a JWT Bearer token. When
 *                         non-null and returns a non-null string, the token
 *                         is sent as `Authorization: Bearer <token>` alongside
 *                         the X-Api-Key header. Required for endpoints that
 *                         enforce user-level auth (/game/finish, /next-training,
 *                         /curriculum/next).
 */
class HttpCoachApiClient(
    val baseUrl: String,
    val apiKey: String,
    val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
    val tokenProvider: (() -> String?)? = null,
    /**
     * Optional sink for the X-Auth-Token refresh header — see
     * [TokenRefresh].  Without this, a user who chats for a full
     * day without ending a game would lose their session even
     * though they're continuously active.
     */
    val tokenSink: ((String) -> Unit)? = null,
) : CoachApiClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val CHAT_PATH = "/chat"
        private const val CHAT_STREAM_PATH = "/chat/stream"
        private const val CHAT_HISTORY_PATH = "/chat/history"
        private const val FEEDBACK_PATH = "/game/coach-feedback"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    /** Build the standard auth header set (X-Api-Key plus optional Bearer token). */
    private fun authHeaders(extraToken: String? = null): Map<String, String> = buildMap {
        put("X-Api-Key", apiKey)
        val bearer = extraToken ?: tokenProvider?.invoke()
        if (bearer != null) put("Authorization", "Bearer $bearer")
    }

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun chat(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto?,
        pastMistakes: List<String>?,
        moveCount: Int?,
        coachVoice: String?,
    ): ApiResult<ChatResponseBody> = withRetry(maxAttempts = 2) {
        http.request(
            path = CHAT_PATH,
            method = "POST",
            headers = authHeaders(),
            body = buildJson(fen, messages, playerProfile, pastMistakes, moveCount, coachVoice),
            onResponse = refreshOnSuccess(),
            parse = ::parseResponse,
        )
    }

    override fun chatStream(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto?,
        pastMistakes: List<String>?,
        moveCount: Int?,
        coachVoice: String?,
        gameId: String?,
    ): Flow<StreamChunk> = channelFlow {
        withContext(Dispatchers.IO) {
            // Declared outside the body try so the finally block can
            // disconnect on any exit path — happy completion, HTTP
            // error, or transport exception.  Without the disconnect,
            // ``inputStream.use {}`` closes the read pipe but the
            // underlying socket can linger up to ``readTimeoutMs``
            // (60 s) before the platform reclaims it.
            //
            // Residual gap: under coroutine cancellation while
            // ``reader.readLine()`` is blocked, the finally only runs
            // after the JVM read returns (worst case: readTimeoutMs).
            // ``Dispatchers.IO`` does not interrupt threads on cancel,
            // so a future improvement is to register a cancel-hook
            // (suspendCancellableCoroutine / runInterruptible) that
            // closes the socket from outside the blocked thread.
            var conn: HttpURLConnection? = null
            try {
                val url = URL("$baseUrl$CHAT_STREAM_PATH")
                conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("Accept", "text/event-stream")
                conn.setRequestProperty("X-Api-Key", apiKey)
                conn.setRequestProperty(COACH_API_VERSION_HEADER, COACH_API_VERSION)
                tokenProvider?.invoke()?.let { token ->
                    conn.setRequestProperty("Authorization", "Bearer $token")
                }
                conn.doOutput = true
                conn.connectTimeout = connectTimeoutMs
                conn.readTimeout = readTimeoutMs

                conn.outputStream.bufferedWriter(Charsets.UTF_8).use {
                    it.write(
                        buildJson(
                            fen, messages, playerProfile, pastMistakes, moveCount, coachVoice, gameId,
                        ),
                    )
                }

                val code = conn.responseCode
                if (code != HttpURLConnection.HTTP_OK) {
                    // DIAGNOSTIC: include the error body (e.g. FastAPI 422
                    // validation detail naming the offending field) so the
                    // client log shows WHY, not just the status code.
                    val errBody = try {
                        conn.errorStream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
                    } catch (_: Exception) {
                        null
                    }
                    val suffix = errBody?.takeIf { it.isNotBlank() }?.let { ": " + it.take(400) } ?: ""
                    send(StreamChunk.StreamError("HTTP $code$suffix"))
                    return@withContext
                }

                // Response headers are available the moment the server
                // commits to a 200 — well before the SSE stream itself
                // starts emitting.  Rotate the JWT now rather than
                // waiting for the stream to close, in case the user
                // backgrounds mid-stream.
                consumeRefreshedToken(conn, tokenSink)

                conn.inputStream.bufferedReader(Charsets.UTF_8).use { reader ->
                    var line: String?
                    while (reader.readLine().also { line = it } != null) {
                        val l = line!!.trim()
                        if (!l.startsWith("data: ")) continue
                        parseStreamChunk(l.removePrefix("data: "))?.let { chunk -> send(chunk) }
                    }
                }
            } catch (_: SocketTimeoutException) {
                send(StreamChunk.StreamError("Timeout"))
            } catch (e: Exception) {
                send(StreamChunk.StreamError(e.message ?: "Network error"))
            } finally {
                // ``disconnect()`` is documented to be safe to call
                // multiple times and on connections that never opened
                // a socket.  Swallow any teardown exception so a
                // dying socket doesn't mask the original error path.
                try {
                    conn?.disconnect()
                } catch (_: Exception) {
                    // intentionally ignored — teardown best-effort
                }
            }
        }
    }

    /**
     * Decode one SSE ``data:`` payload from /chat/stream into a
     * [StreamChunk].  Three discriminator values, all carrying a
     * top-level ``type`` field:
     *
     *   - ``{"type":"chunk","text":"..."}``               → [StreamChunk.Chunk]
     *   - ``{"type":"done","engine_signal":...,"mode":...}`` → [StreamChunk.Done]
     *   - ``{"type":"abort","reply":...,"engine_signal":...,"mode":...}`` → [StreamChunk.Abort]
     *   - ``{"type":"error","message":"..."}``            → [StreamChunk.StreamError]
     *
     * Decoded as a generic [kotlinx.serialization.json.JsonObject] so the
     * type tag can be inspected before committing to a concrete shape —
     * keeps the parser tolerant of new/unknown event types (returns null,
     * which the caller drops).
     */
    private fun parseStreamChunk(text: String): StreamChunk? =
        try {
            val root = ApiJson.parseToJsonElement(text).jsonObject
            when (root["type"]?.jsonPrimitive?.contentOrNull) {
                "chunk" -> StreamChunk.Chunk(
                    root["text"]?.jsonPrimitive?.contentOrNull ?: ""
                )
                "done" -> {
                    val signalEl = root["engine_signal"]?.takeUnless { it is JsonNull }
                    val engineSignal = signalEl?.let {
                        ApiJson.decodeFromJsonElement(EngineSignalDto.serializer(), it)
                    }
                    StreamChunk.Done(
                        engineSignal = engineSignal,
                        mode = root["mode"]?.jsonPrimitive?.contentOrNull ?: "CHAT_V1",
                    )
                }
                "abort" -> {
                    val signalEl = root["engine_signal"]?.takeUnless { it is JsonNull }
                    val engineSignal = signalEl?.let {
                        ApiJson.decodeFromJsonElement(EngineSignalDto.serializer(), it)
                    }
                    StreamChunk.Abort(
                        reply = root["reply"]?.jsonPrimitive?.contentOrNull ?: "",
                        engineSignal = engineSignal,
                        mode = root["mode"]?.jsonPrimitive?.contentOrNull ?: "CHAT_V1",
                    )
                }
                "error" -> StreamChunk.StreamError(
                    root["message"]?.jsonPrimitive?.contentOrNull ?: "Server error"
                )
                else -> null
            }
        } catch (_: Exception) {
            null
        }

    override suspend fun submitFeedback(
        fen: String,
        isHelpful: Boolean,
        token: String?,
    ): ApiResult<Unit> = http.requestNoBody(
        path = FEEDBACK_PATH,
        method = "POST",
        headers = authHeaders(extraToken = token),
        body = ApiJson.encodeToString(
            CoachFeedbackRequest(sessionFen = fen, isHelpful = isHelpful)
        ),
        onResponse = refreshOnSuccess(),
    )

    override suspend fun getHistory(
        limit: Int,
        gameId: String?,
    ): ApiResult<ChatHistoryResponseBody> {
        // Scope history to the current game when present (per-game threads);
        // omit game_id → player-global history (server default).
        val path = buildString {
            append(CHAT_HISTORY_PATH).append("?limit=").append(limit)
            gameId?.takeIf { it.isNotBlank() }?.let {
                append("&game_id=").append(java.net.URLEncoder.encode(it, "UTF-8"))
            }
        }
        return http.request(
            path = path,
            method = "GET",
            headers = authHeaders(),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<ChatHistoryResponseBody>(body) },
        )
    }

    // -----------------------------------------------------------------------
    // JSON serialisation / deserialisation (private — not unit tested directly)
    // -----------------------------------------------------------------------

    /**
     * Encode the /chat (and /chat/stream) request payload.  Null
     * optional fields are dropped by the shared [ApiJson]
     * ``encodeDefaults = false`` config so the wire shape matches
     * the pre-migration ``buildJson`` output (omit-when-null for
     * ``player_profile`` / ``past_mistakes`` / ``move_count`` /
     * ``coach_voice``).  ``coachVoice`` is normalised to null when
     * blank to preserve parity with the prior ``isNotBlank`` guard.
     */
    private fun buildJson(
        fen: String,
        messages: List<ChatMessageDto>,
        playerProfile: PlayerProfileDto?,
        pastMistakes: List<String>?,
        moveCount: Int?,
        coachVoice: String?,
        gameId: String? = null,
    ): String = ApiJson.encodeToString(
        ChatRequestBody(
            fen = fen,
            messages = messages,
            playerProfile = playerProfile,
            pastMistakes = pastMistakes,
            moveCount = moveCount,
            coachVoice = coachVoice?.takeIf { it.isNotBlank() },
            gameId = gameId?.takeIf { it.isNotBlank() },
        )
    )

    private fun parseResponse(body: String): ChatResponseBody =
        ApiJson.decodeFromString<ChatResponseBody>(body)
}
