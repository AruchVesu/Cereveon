package com.cereveon.myapp

import java.net.HttpURLConnection

/**
 * Client for the post-game AI review endpoints
 * (docs/API_CONTRACTS.md §39/§39a).
 *
 * Returns [ApiResult] on every call — callers never see raw
 * exceptions.  Both endpoints require Bearer auth and forward the
 * ``X-Auth-Token`` rotation header to [tokenSink] on success, same as
 * [HttpLichessApiClient].
 *
 * Default implementations return [ApiResult.HttpError(501)] so test
 * fakes can override only the methods they exercise.
 */
interface ReviewApiClient {

    /**
     * POST /game/{event_id}/review — start, coalesce, or retry the
     * review job.  202 while worker work is in flight, 200 when a
     * complete row already answers; both parse to the same
     * [GameReviewResponse] and the caller just polls [getReview]
     * until [GameReviewResponse.isTerminal].
     *
     * Never 402s: past the free cap the review still completes with
     * `llm.outcome == "skipped_entitlement"` + the quota snapshot.
     *
     * @return HttpError(400) for ineligible games (in-app source /
     *         too short), (403) another player's game, (404) unknown
     *         event, (429) rate limit.
     */
    suspend fun startReview(eventId: String, token: String): ApiResult<GameReviewResponse> =
        ApiResult.HttpError(501)

    /**
     * GET /game/{event_id}/review — poll the row.  404 while no review
     * exists at the current analysis version (the UI shows the "Get
     * coach review" state).
     */
    suspend fun getReview(eventId: String, token: String): ApiResult<GameReviewResponse> =
        ApiResult.HttpError(501)
}

/**
 * Production [ReviewApiClient] backed by [BaseHttpClient].
 *
 * @param baseUrl   Scheme + host + optional port, no trailing slash.
 * @param tokenSink Sink for the ``X-Auth-Token`` refresh header; null
 *                  disables rotation (test fakes).
 */
class HttpReviewApiClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : ReviewApiClient {

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    private fun bearerHeader(token: String): Map<String, String> =
        mapOf("Authorization" to "Bearer $token")

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    // eventId is a server-issued UUID from /game/history (RFC 4122
    // grammar), safe to concatenate without URL-encoding — same
    // reasoning as HttpLichessApiClient.getImportJob.
    private fun reviewPath(eventId: String) = "/game/$eventId/review"

    override suspend fun startReview(
        eventId: String,
        token: String,
    ): ApiResult<GameReviewResponse> = http.request(
        path = reviewPath(eventId),
        method = "POST",
        headers = bearerHeader(token),
        // 202 = job dispatched, 200 = an existing complete row answered;
        // both carry the same body and the poll loop treats them alike.
        successCodes = setOf(
            HttpURLConnection.HTTP_OK,
            HttpURLConnection.HTTP_ACCEPTED,
        ),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameReviewResponse>(body) },
    )

    override suspend fun getReview(
        eventId: String,
        token: String,
    ): ApiResult<GameReviewResponse> = http.request(
        path = reviewPath(eventId),
        method = "GET",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<GameReviewResponse>(body) },
    )
}
