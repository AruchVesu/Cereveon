package com.cereveon.myapp

import kotlinx.serialization.encodeToString

/**
 * Client for POST /engine/eval (server.py — migrated from host_app.py
 * in the 2026-05-12 retirement pass).
 *
 * The endpoint requires X-Api-Key (or Bearer SECA_API_KEY) auth.
 * Implementations are safe to call from any coroutine context; I/O
 * dispatch is handled internally.
 */
interface EngineEvalClient {

    /**
     * Evaluate the position given by [fen] using the Stockfish engine.
     *
     * Returns [ApiResult.Success] with an [EngineEvalResponse] on HTTP 200;
     * [ApiResult.HttpError] on any non-200 response; [ApiResult.Timeout] when
     * the connect or read deadline is exceeded; [ApiResult.NetworkError] for
     * all other transport failures.
     *
     * @param fen Board position in FEN notation.
     */
    suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse>
}

/**
 * Production implementation of [EngineEvalClient] backed by [BaseHttpClient].
 *
 * Each [evaluate] call opens its own connection; the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash
 *                         (e.g. "http://10.0.2.2:8000").
 * @param apiKey           X-Api-Key value.  Sent on every call when
 *                         non-empty; the server-side route is auth-gated
 *                         (Sprint 4.x host_app retirement tightened the
 *                         contract from unauthenticated to X-Api-Key /
 *                         Bearer).  An empty key here will surface as 401.
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 */
class HttpEngineEvalClient(
    val baseUrl: String,
    val apiKey: String = "",
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
) : EngineEvalClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val EVAL_PATH = "/engine/eval"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse> =
        withRetry(maxAttempts = 2) {
            val headers = if (apiKey.isNotEmpty()) mapOf("X-Api-Key" to apiKey) else emptyMap()
            http.request(
                path = EVAL_PATH,
                method = "POST",
                headers = headers,
                body = ApiJson.encodeToString(EngineEvalRequest(fen = fen)),
                parse = { body -> ApiJson.decodeFromString<EngineEvalResponse>(body) },
            )
        }
}
