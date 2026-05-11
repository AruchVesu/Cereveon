package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/**
 * Shared HTTP helper for the production API clients.
 *
 * Each concrete client (`CoachApiClient`, `AuthApiClient`, `GameApiClient`,
 * `EngineEvalApiClient`, `LiveMoveApiClient`) used to inline the same
 * ~25-line HTTP ceremony — open connection, set headers, set timeouts,
 * write the JSON body, branch on status code, map `SocketTimeoutException`
 * to [ApiResult.Timeout], map other exceptions to [ApiResult.NetworkError].
 * The audit (Sprint 4 review) called this out as the highest-leverage
 * duplication in the Android client: `GameApiClient.kt` alone carried
 * 15 nearly-identical copies of this block.
 *
 * This class centralises the ceremony into a single suspend function
 * [request]; concrete clients now only provide the path, method, headers,
 * body, and a per-method `parse` lambda.  Behavioural parity with the
 * pre-refactor clients is the load-bearing property:
 *
 *  - Same status-code branching: 200 → success; any other code →
 *    [ApiResult.HttpError(code)].
 *  - Same exception-to-result mapping: `SocketTimeoutException` →
 *    [ApiResult.Timeout]; everything else → [ApiResult.NetworkError].
 *  - Same response-body discipline: body is read only on 200; non-200
 *    responses are NOT consumed (preserves the existing pattern across
 *    auth/coach/game/engine/live-move clients).
 *  - `X-API-Version` is set on every request (matches the existing
 *    behaviour pinned by `ApiVersionHeaderTest`).
 *  - `Content-Type: application/json` is set automatically when [body]
 *    is non-null (matches existing POST/PATCH behaviour).
 *
 * The [onResponse] hook is for endpoints that need to inspect the
 * response *before* the parser runs — currently used only by the auth
 * client's me/updateMe/changePassword endpoints to consume the
 * `X-Auth-Token` rotation header (see [consumeRefreshedToken]).
 *
 * I/O is dispatched on [Dispatchers.IO]; callers can invoke from any
 * coroutine context.
 */
class BaseHttpClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
) {
    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = 8_000
        const val DEFAULT_READ_TIMEOUT_MS = 15_000
    }

    /**
     * Perform an HTTP request and map the response to [ApiResult].
     *
     * @param path       URL path (no scheme/host).  Concatenated onto [baseUrl].
     * @param method     HTTP method (GET / POST / PATCH / DELETE).
     * @param headers    Extra request headers.  `X-API-Version` is always set
     *                   automatically; `Content-Type: application/json` is set
     *                   automatically when [body] is non-null.
     * @param body         Request body string.  When non-null, the connection is
     *                     put in output mode, `Content-Type: application/json` is
     *                     set, and the body is written as UTF-8 bytes.
     * @param successCodes HTTP codes treated as success.  Defaults to `{200}`;
     *                     widen for endpoints that return 201 Created (auth
     *                     register).
     * @param onResponse   Optional hook called with the [HttpURLConnection] AFTER
     *                     a successful response is read but BEFORE [parse] runs.
     *                     Used to inspect response headers (e.g. the
     *                     `X-Auth-Token` refresh header on auth endpoints).
     * @param parse        Function that converts the response body text into [T].
     */
    suspend fun <T> request(
        path: String,
        method: String,
        headers: Map<String, String> = emptyMap(),
        body: String? = null,
        successCodes: Set<Int> = setOf(HttpURLConnection.HTTP_OK),
        onResponse: ((HttpURLConnection) -> Unit)? = null,
        parse: (String) -> T,
    ): ApiResult<T> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$baseUrl$path")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = method
            conn.setRequestProperty(COACH_API_VERSION_HEADER, COACH_API_VERSION)
            headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            if (body != null) {
                conn.setRequestProperty("Content-Type", "application/json")
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }

            val code = conn.responseCode
            if (code in successCodes) {
                // HttpURLConnection.inputStream throws FileNotFoundException
                // on any code >= 400 — the bytes live on errorStream
                // instead.  Most successCodes are 2xx (where inputStream
                // is the right channel), but callers can opt into wider
                // sets (e.g. getActiveGame treats 404 as Success(null)).
                // Pick the stream that actually has the body for the
                // observed code, and tolerate a null errorStream (some
                // 4xx responses have no body at all).
                val stream =
                    if (code >= 400) (conn.errorStream ?: java.io.ByteArrayInputStream(ByteArray(0)))
                    else conn.inputStream
                val text = stream.bufferedReader(Charsets.UTF_8).readText()
                onResponse?.invoke(conn)
                ApiResult.Success(parse(text))
            } else {
                ApiResult.HttpError(code)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    /**
     * Convenience for endpoints whose success response has no useful body
     * (POST /auth/logout, DELETE /repertoire/{eco} when the caller doesn't
     * need the response).  Returns [ApiResult.Success(Unit)] on success.
     */
    suspend fun requestNoBody(
        path: String,
        method: String,
        headers: Map<String, String> = emptyMap(),
        body: String? = null,
        successCodes: Set<Int> = setOf(HttpURLConnection.HTTP_OK),
        onResponse: ((HttpURLConnection) -> Unit)? = null,
    ): ApiResult<Unit> = request(
        path = path,
        method = method,
        headers = headers,
        body = body,
        successCodes = successCodes,
        onResponse = onResponse,
        parse = { Unit },
    )
}
