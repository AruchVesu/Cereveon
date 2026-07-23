package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString

/**
 * Client for POST /feedback (docs/API_CONTRACTS.md §38).
 *
 * Persists one free-form "Send feedback" message from the game drawer's
 * form.  Fire-and-forget from the product's perspective: the server
 * stores the row for the operator to read; nothing is read back into
 * coaching.  The route is `Depends(get_current_player)` on the server,
 * so a valid `Authorization: Bearer <jwt>` is required — same posture
 * as [BillingApiClient].
 */
interface FeedbackApiClient {

    /**
     * Submit [message] (server trims + enforces 1..2000 chars) with an
     * optional [appVersion] (`BuildConfig.VERSION_NAME`; server caps at
     * 64 chars).
     *
     * @return [ApiResult.Success] with the receipt on HTTP 200;
     *         [ApiResult.HttpError] on non-200 (422 = blank/overlong
     *         message, 429 = rate limited); [ApiResult.Timeout] /
     *         [ApiResult.NetworkError] for transport failures.
     */
    suspend fun submitFeedback(
        message: String,
        appVersion: String?,
    ): ApiResult<FeedbackSubmitResponse>
}

/**
 * Request body for POST /feedback.
 *
 * [message]     Free-form feedback text.
 * [appVersion]  Client build version; omitted from the wire when null
 *               (`encodeDefaults = false` in [ApiJson]).
 */
@Serializable
data class FeedbackSubmitRequest(
    val message: String,
    @SerialName("app_version") val appVersion: String? = null,
)

/**
 * Response from POST /feedback.
 *
 * [status]  Fixed literal "received" on success.
 * [id]      Server-issued row id (support-conversation reference).
 */
@Serializable
data class FeedbackSubmitResponse(
    val status: String = "",
    val id: String = "",
)

/**
 * Production implementation backed by [BaseHttpClient] /
 * [java.net.HttpURLConnection].  Each call opens its own connection;
 * the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param tokenProvider    Supplier of the JWT Bearer token.  Required (no
 *                         default) — the route is a certain 401 without it,
 *                         same rationale as [HttpBillingApiClient.tokenProvider].
 * @param tokenSink        Optional sink for the X-Auth-Token refresh header
 *                         so the submit call participates in JWT rotation
 *                         (docs/API_CONTRACTS.md §10).
 */
class HttpFeedbackApiClient(
    val baseUrl: String,
    val apiKey: String,
    val tokenProvider: () -> String?,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : FeedbackApiClient {

    companion object {
        private const val FEEDBACK_PATH = "/feedback"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun submitFeedback(
        message: String,
        appVersion: String?,
    ): ApiResult<FeedbackSubmitResponse> = http.request(
        path = FEEDBACK_PATH,
        method = "POST",
        headers = buildMap {
            put("X-Api-Key", apiKey)
            tokenProvider.invoke()?.let { put("Authorization", "Bearer $it") }
        },
        body = ApiJson.encodeToString(
            FeedbackSubmitRequest(message = message, appVersion = appVersion)
        ),
        onResponse = { conn -> consumeRefreshedToken(conn, tokenSink) },
        parse = { body -> ApiJson.decodeFromString<FeedbackSubmitResponse>(body) },
    )
}
