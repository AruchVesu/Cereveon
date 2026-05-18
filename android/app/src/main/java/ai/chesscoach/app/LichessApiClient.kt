package ai.chesscoach.app

import kotlinx.serialization.encodeToString
import java.net.HttpURLConnection

/**
 * Shared client interface for the backend Lichess integration endpoints.
 *
 * Returns [ApiResult] on every call — callers never see raw exceptions.
 * Implementations are safe to call from any coroutine context.
 *
 * All endpoints require Bearer-token auth.  Successful responses
 * (2xx) carry an ``X-Auth-Token`` rotation header that
 * [HttpLichessApiClient] forwards to its [tokenSink] for transparent
 * JWT refresh — same pattern as [HttpAuthApiClient].
 *
 * Default implementations return [ApiResult.HttpError(501)] so test
 * fakes can override only the methods they exercise.
 */
interface LichessApiClient {

    /**
     * GET /lichess/status.
     *
     * Returns the player's current Lichess link state plus the count of
     * games imported so far.  When [LichessStatusResponse.linked] is
     * false the remaining fields are absent on the wire (null/default
     * here).
     */
    suspend fun status(token: String): ApiResult<LichessStatusResponse> =
        ApiResult.HttpError(501)

    /**
     * POST /lichess/link.
     *
     * @return [ApiResult.Success] with [LichessLinkResponse] on HTTP 200.
     *         [ApiResult.HttpError(404)] when the Lichess username does
     *         not exist; (409) when the handle is already linked to
     *         another ChessCoach player; (400) on schema validation;
     *         (502/503) on Lichess upstream / rate-limit; transport
     *         variants otherwise.
     */
    suspend fun link(username: String, token: String): ApiResult<LichessLinkResponse> =
        ApiResult.HttpError(501)

    /**
     * POST /lichess/import?max_games=N.
     *
     * Synchronous — returns when the slice is complete (or the cap is
     * reached).  Repeated calls walk forward through history via the
     * server-side ``last_imported_at`` watermark.
     *
     * @return [ApiResult.Success] with [LichessImportResponse] on HTTP 200.
     *         [ApiResult.HttpError(400)] when the player has no Lichess
     *         link (link first); (502) on Lichess upstream; (503) when
     *         Lichess rate-limits.
     */
    suspend fun importGames(
        token: String,
        maxGames: Int = DEFAULT_MAX_IMPORT,
        rated: Boolean = true,
    ): ApiResult<LichessImportResponse> = ApiResult.HttpError(501)

    /**
     * DELETE /lichess/link.
     *
     * Idempotent — returns ``{"unlinked": false}`` when no link existed.
     * Imported ``game_events`` rows are retained as history (backend
     * policy).
     */
    suspend fun unlink(token: String): ApiResult<LichessUnlinkResponse> =
        ApiResult.HttpError(501)

    companion object {
        /**
         * Default per-call import slice, mirroring the backend default
         * (``max_games`` Query param defaults to 50; server hard cap is
         * 100).  The Connect bottom sheet's "Import games" button uses
         * this when the user doesn't override.
         */
        const val DEFAULT_MAX_IMPORT = 50
    }
}

/**
 * Production [LichessApiClient] backed by [BaseHttpClient].
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param connectTimeoutMs TCP connect deadline.
 * @param readTimeoutMs    Read deadline.  The import endpoint may take
 *                         30–60s for a full 100-game slice; if the
 *                         caller bumps the slice cap, also bump this.
 * @param tokenSink        Optional sink for the ``X-Auth-Token`` refresh
 *                         header — every successful response hands the
 *                         freshly-minted JWT here so callers can rotate
 *                         their stored token transparently.  Null
 *                         disables rotation (test fakes).
 */
class HttpLichessApiClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_LICHESS_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : LichessApiClient {

    companion object {
        /**
         * Lichess import streams NDJSON and re-runs Stockfish-free; a
         * 100-game pull on a thibault-scale account is ~30–60s of
         * server-side work plus network.  Default the read budget
         * higher than the auth client's 15s so the import call doesn't
         * Timeout on the happy path.  Callers can override per
         * environment.
         */
        const val DEFAULT_LICHESS_READ_TIMEOUT_MS = 90_000

        private const val STATUS_PATH = "/lichess/status"
        private const val LINK_PATH = "/lichess/link"
        private const val IMPORT_PATH = "/lichess/import"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    private fun bearerHeader(token: String): Map<String, String> =
        mapOf("Authorization" to "Bearer $token")

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun status(token: String): ApiResult<LichessStatusResponse> =
        http.request(
            path = STATUS_PATH,
            method = "GET",
            headers = bearerHeader(token),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<LichessStatusResponse>(body) },
        )

    override suspend fun link(
        username: String,
        token: String,
    ): ApiResult<LichessLinkResponse> = http.request(
        path = LINK_PATH,
        method = "POST",
        headers = bearerHeader(token),
        body = ApiJson.encodeToString(LichessLinkRequest(username = username)),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessLinkResponse>(body) },
    )

    override suspend fun importGames(
        token: String,
        maxGames: Int,
        rated: Boolean,
    ): ApiResult<LichessImportResponse> = http.request(
        // Query parameters appended to the path — BaseHttpClient
        // does not URL-encode for us, but ``maxGames`` is an Int and
        // ``rated`` is a Boolean so neither needs encoding.  The
        // backend caps maxGames at 100; sending a value above that
        // yields HTTP 422 (FastAPI Query validator), which falls
        // through to ApiResult.HttpError(422) here.
        path = "$IMPORT_PATH?max_games=$maxGames&rated=$rated",
        method = "POST",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessImportResponse>(body) },
    )

    override suspend fun unlink(token: String): ApiResult<LichessUnlinkResponse> =
        http.request(
            path = LINK_PATH,
            method = "DELETE",
            headers = bearerHeader(token),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<LichessUnlinkResponse>(body) },
        )
}
