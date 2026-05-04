package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/**
 * Client for POST /engine/eval (host_app.py).
 *
 * The endpoint requires **no authentication** — the X-Api-Key header is not
 * sent.  Implementations are safe to call from any coroutine context; I/O
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
     * @param fen Board position in FEN notation or "startpos".
     */
    suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse>
}

/**
 * Production implementation of [EngineEvalClient] backed by [HttpURLConnection].
 *
 * Each [evaluate] call opens its own connection; the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash
 *                         (e.g. "http://10.0.2.2:8000").
 * @param apiKey           Optional X-Api-Key value. Sent only when non-empty, so
 *                         existing callers that omit it continue to work against
 *                         the unauthenticated `/engine/eval` endpoint.
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 */
class HttpEngineEvalClient(
    val baseUrl: String,
    val apiKey: String = "",
    val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
) : EngineEvalClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = 8_000
        const val DEFAULT_READ_TIMEOUT_MS = 15_000
        private const val EVAL_PATH = "/engine/eval"
    }

    override suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse> =
        withRetry(maxAttempts = 2) {
            withContext(Dispatchers.IO) {
                try {
                    val url = URL("$baseUrl$EVAL_PATH")
                    val conn = url.openConnection() as HttpURLConnection
                    conn.requestMethod = "POST"
                    conn.setRequestProperty("Content-Type", "application/json")
                    if (apiKey.isNotEmpty()) conn.setRequestProperty("X-Api-Key", apiKey)
                    conn.doOutput = true
                    conn.connectTimeout = connectTimeoutMs
                    conn.readTimeout = readTimeoutMs

                    val body = JSONObject().put("fen", fen).toString()
                    conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }

                    val code = conn.responseCode
                    if (code == HttpURLConnection.HTTP_OK) {
                        val text = conn.inputStream.bufferedReader(Charsets.UTF_8).readText()
                        ApiResult.Success(parseResponse(text))
                    } else {
                        ApiResult.HttpError(code)
                    }
                } catch (_: SocketTimeoutException) {
                    ApiResult.Timeout
                } catch (e: Exception) {
                    ApiResult.NetworkError(e)
                }
            }
        }

    private fun parseResponse(body: String): EngineEvalResponse {
        val root = JSONObject(body)
        val score = if (root.isNull("score")) null else root.optInt("score")
        val bestMove = root.optString("best_move", "").ifEmpty { null }
        val source = root.optString("source", "engine")
        return EngineEvalResponse(score = score, bestMove = bestMove, source = source)
    }
}
