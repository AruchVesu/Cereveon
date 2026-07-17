package ai.chesscoach.app

import java.net.HttpURLConnection

/**
 * Client interface for the in-app notification feed endpoints
 * (docs/API_CONTRACTS.md §40).
 *
 * Returns [ApiResult] on every call — callers never see raw
 * exceptions.  All endpoints require Bearer-token auth; successful
 * responses carry the ``X-Auth-Token`` rotation header that
 * [HttpNotificationsApiClient] forwards to its [tokenSink] — same
 * pattern as [HttpLichessApiClient].
 *
 * Default implementations return [ApiResult.HttpError(501)] so test
 * fakes can override only the methods they exercise.
 */
interface NotificationsApiClient {

    /** GET /notifications — visible feed rows (newest first) + badge count. */
    suspend fun feed(token: String): ApiResult<NotificationsFeedResponse> =
        ApiResult.HttpError(501)

    /** POST /notifications/{id}/read — mark one row read (idempotent). */
    suspend fun markRead(
        notificationId: String,
        token: String,
    ): ApiResult<NotificationReadResponse> = ApiResult.HttpError(501)

    /** POST /notifications/read-all — mark every visible row read. */
    suspend fun markAllRead(token: String): ApiResult<NotificationsReadAllResponse> =
        ApiResult.HttpError(501)

    /** POST /notifications/{id}/dismiss — soft-delete one row from the feed. */
    suspend fun dismiss(
        notificationId: String,
        token: String,
    ): ApiResult<NotificationDismissResponse> = ApiResult.HttpError(501)
}

/**
 * Production [NotificationsApiClient] backed by [BaseHttpClient].
 *
 * @param baseUrl   Scheme + host + optional port, no trailing slash.
 * @param tokenSink Optional sink for the ``X-Auth-Token`` refresh
 *                  header — every successful response hands the
 *                  freshly-minted JWT here so callers can rotate their
 *                  stored token transparently.  Null disables rotation
 *                  (test fakes).
 */
class HttpNotificationsApiClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : NotificationsApiClient {

    companion object {
        private const val FEED_PATH = "/notifications"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    private fun bearerHeader(token: String): Map<String, String> =
        mapOf("Authorization" to "Bearer $token")

    private fun refreshOnSuccess(): (HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun feed(token: String): ApiResult<NotificationsFeedResponse> =
        http.request(
            path = FEED_PATH,
            method = "GET",
            headers = bearerHeader(token),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<NotificationsFeedResponse>(body) },
        )

    override suspend fun markRead(
        notificationId: String,
        token: String,
    ): ApiResult<NotificationReadResponse> = http.request(
        // notificationId is a server-issued UUID (RFC 4122 grammar,
        // path-safe verbatim) — same no-encode rationale as
        // HttpLichessApiClient.getImportJob.
        path = "$FEED_PATH/$notificationId/read",
        method = "POST",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<NotificationReadResponse>(body) },
    )

    override suspend fun markAllRead(token: String): ApiResult<NotificationsReadAllResponse> =
        http.request(
            path = "$FEED_PATH/read-all",
            method = "POST",
            headers = bearerHeader(token),
            onResponse = refreshOnSuccess(),
            parse = { body -> ApiJson.decodeFromString<NotificationsReadAllResponse>(body) },
        )

    override suspend fun dismiss(
        notificationId: String,
        token: String,
    ): ApiResult<NotificationDismissResponse> = http.request(
        path = "$FEED_PATH/$notificationId/dismiss",
        method = "POST",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<NotificationDismissResponse>(body) },
    )
}
