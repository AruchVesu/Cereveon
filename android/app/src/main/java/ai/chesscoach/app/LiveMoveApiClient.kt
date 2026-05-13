package ai.chesscoach.app

import kotlinx.serialization.encodeToString

/**
 * Client for POST /live/move (server.py).
 *
 * Returns a [LiveMoveResponse] containing a per-move coaching hint from the
 * backend live coaching pipeline.  The hint always references the engine
 * evaluation band, game phase, and move quality.
 *
 * The server route is gated by `Depends(get_current_player)` (see
 * `llm/server.py::live_move`), so a valid `Authorization: Bearer <jwt>`
 * header is required on every call.  X-Api-Key is accepted by the
 * upstream proxy / shared dependency chain but does not by itself
 * satisfy `/live/move` — without a Bearer the route returns
 * 401 "Missing token" and no Mode-1 hint is ever produced.
 * Implementations are safe to call from any coroutine context.
 */
interface LiveMoveClient {

    /**
     * Fetch a coaching hint for the move just played.
     *
     * @param fen       Board position after the move in FEN notation.
     * @param uci       The move just played in UCI notation (e.g. "e2e4").
     * @param playerId  Player identifier (reserved for future enrichment).
     * @return [ApiResult.Success] with a [LiveMoveResponse] on HTTP 200;
     *         [ApiResult.HttpError] on non-200; [ApiResult.Timeout] on deadline
     *         exceeded; [ApiResult.NetworkError] for all other failures.
     */
    suspend fun getLiveCoaching(
        fen: String,
        uci: String,
        playerId: String = "demo",
    ): ApiResult<LiveMoveResponse>
}

/**
 * Production implementation of [LiveMoveClient] backed by [HttpURLConnection].
 *
 * Each call opens its own connection; the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 * @param tokenProvider    Supplier of the JWT Bearer token for `/live/move`.
 *                         The route is `Depends(get_current_player)` on the
 *                         server (llm/server.py::live_move), so it returns
 *                         401 "Missing token" without an `Authorization:
 *                         Bearer <jwt>` header — silently breaking the
 *                         Mode-1 inline hint for every authenticated user.
 *                         When the lambda returns null (logged-out window)
 *                         the request is sent without the header and the
 *                         server's 401 path handles it cleanly via the
 *                         existing `ApiResult.HttpError(401)` branch in
 *                         `ChessViewModel.dispatchHumanMoveCoach`.
 * @param tokenSink        Optional sink for the X-Auth-Token refresh header.
 *                         The `/live/move` route depends on `get_current_player`
 *                         (llm/server.py — `Depends(get_current_player)`), so the
 *                         server attaches a freshly-minted JWT to every 200
 *                         response.  Wiring [tokenSink] lets a long live-coach
 *                         session rotate the stored JWT continuously, instead of
 *                         dropping the rotation header on the floor and forcing
 *                         a re-login at the 24 h JWT exp.  See
 *                         docs/API_CONTRACTS.md §10 (`X-Auth-Token` refresh
 *                         header) and [TokenRefresh] for the helper.
 *                         Default `null` preserves existing callers; pass a
 *                         non-null sink to participate in rotation.
 */
class HttpLiveMoveClient(
    val baseUrl: String,
    val apiKey: String,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenProvider: (() -> String?)? = null,
    val tokenSink: ((String) -> Unit)? = null,
) : LiveMoveClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val LIVE_MOVE_PATH = "/live/move"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun getLiveCoaching(
        fen: String,
        uci: String,
        playerId: String,
    ): ApiResult<LiveMoveResponse> = http.request(
        path = LIVE_MOVE_PATH,
        method = "POST",
        headers = buildMap {
            put("X-Api-Key", apiKey)
            tokenProvider?.invoke()?.let { put("Authorization", "Bearer $it") }
        },
        body = ApiJson.encodeToString(
            LiveMoveRequest(fen = fen, uci = uci, playerId = playerId)
        ),
        onResponse = { conn -> consumeRefreshedToken(conn, tokenSink) },
        parse = { body -> ApiJson.decodeFromString<LiveMoveResponse>(body) },
    )
}
