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
     * POST /lichess/import?max_games=N (v1 synchronous path).
     *
     * Synchronous — blocks the request until the slice is complete (or
     * the cap is reached).  Repeated calls walk forward through history
     * via the server-side ``last_imported_at`` watermark.
     *
     * Retained for backward compat with test fakes (and any caller
     * still wired to the v1 contract).  Production code paths should
     * use [startImport] + [getImportJob] instead — the v2 surface
     * returns 202 immediately and lets the UI render a determinate
     * progress bar via polling.
     *
     * @return [ApiResult.Success] with [LichessImportResponse] on HTTP 200.
     *         [ApiResult.HttpError(400)] when the player has no Lichess
     *         link (link first); (502) on Lichess upstream; (503) when
     *         Lichess rate-limits.
     */
    @Deprecated(
        message = "v1 synchronous import. Use startImport(...) + getImportJob(...) for v2 async.",
        replaceWith = ReplaceWith("startImport(token, maxGames, rated)"),
        level = DeprecationLevel.WARNING,
    )
    suspend fun importGames(
        token: String,
        maxGames: Int = DEFAULT_MAX_IMPORT,
        rated: Boolean = true,
    ): ApiResult<LichessImportResponse> = ApiResult.HttpError(501)

    /**
     * POST /lichess/import?max_games=N with ``X-API-Version: 2`` —
     * the v2 async path.
     *
     * Returns 202 immediately with a [LichessImportAccepted] payload.
     * The actual Lichess stream runs on a server-side worker thread;
     * the caller should poll [getImportJob] every ~2s until
     * ``status`` is terminal (``succeeded`` / ``failed``).
     *
     * Coalescing: a second call with an already-running job for the
     * same player returns the existing job_id (and its current
     * counters) rather than spawning a second worker.
     *
     * @return [ApiResult.Success] with [LichessImportAccepted] on HTTP 202.
     *         [ApiResult.HttpError(400)] when the player has no Lichess
     *         link; (503) when Lichess rate-limits during the worker's
     *         own pull (rare — the job row records the failure, the
     *         POST itself succeeds).
     */
    suspend fun startImport(
        token: String,
        maxGames: Int = DEFAULT_MAX_IMPORT,
        rated: Boolean = true,
    ): ApiResult<LichessImportAccepted> = ApiResult.HttpError(501)

    /**
     * GET /lichess/import/job/{job_id} — poll an in-flight import.
     *
     * Owner-scoped: returns HTTP 404 when the job does not exist OR
     * when it belongs to another player.  The Connect sheet's polling
     * loop maps 404 to "give up + revert to last-known Linked state"
     * because either branch means there is nothing to display.
     */
    suspend fun getImportJob(
        jobId: String,
        token: String,
    ): ApiResult<LichessImportJobStatus> = ApiResult.HttpError(501)

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

    @Suppress("OVERRIDE_DEPRECATION")
    @Deprecated(
        message = "v1 synchronous import. Use startImport(...) + getImportJob(...) for v2 async.",
        replaceWith = ReplaceWith("startImport(token, maxGames, rated)"),
        level = DeprecationLevel.WARNING,
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
        //
        // NOTE: this method is retained for tests and is NOT what
        // the production v2 client invokes.  Because ``BaseHttpClient``
        // always sends ``X-API-Version: 2`` (post the version bump),
        // hitting this path would return the v2 202 body which would
        // fail to deserialise as LichessImportResponse.  Production
        // code calls [startImport] instead.
        path = "$IMPORT_PATH?max_games=$maxGames&rated=$rated",
        method = "POST",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessImportResponse>(body) },
    )

    override suspend fun startImport(
        token: String,
        maxGames: Int,
        rated: Boolean,
    ): ApiResult<LichessImportAccepted> = http.request(
        // Same path as v1 importGames; the version branch happens
        // server-side via the ``X-API-Version: 2`` header that
        // BaseHttpClient injects on every request.  The server
        // returns 202 + the job payload on the v2 path.
        path = "$IMPORT_PATH?max_games=$maxGames&rated=$rated",
        method = "POST",
        headers = bearerHeader(token),
        // 202 Accepted IS the v2 success (the KDoc above says so) — but
        // BaseHttpClient's default successCodes is {200}, so every
        // successful import used to land in the HttpError(202) branch:
        // the sheet showed the "unknown error" toast, no progress bar,
        // and the games "mysteriously" appeared later via the
        // activeImportJobId resume path.  Same widening the sibling
        // ReviewApiClient.startReview ships for its own 202 contract.
        successCodes = setOf(HttpURLConnection.HTTP_OK, HttpURLConnection.HTTP_ACCEPTED),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessImportAccepted>(body) },
    )

    override suspend fun getImportJob(
        jobId: String,
        token: String,
    ): ApiResult<LichessImportJobStatus> = http.request(
        // jobId is a server-issued UUID (regex-safe per RFC 4122);
        // we still concatenate without URL-encoding because the
        // backend's ``/import/job/{job_id}`` path-converter
        // accepts the UUID grammar verbatim.
        path = "$IMPORT_PATH/job/$jobId",
        method = "GET",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<LichessImportJobStatus>(body) },
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
