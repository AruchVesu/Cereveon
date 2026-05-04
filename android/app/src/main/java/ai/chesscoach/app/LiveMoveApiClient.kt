package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/**
 * Client for POST /live/move (server.py).
 *
 * Returns a [LiveMoveResponse] containing a per-move coaching hint from the
 * backend live coaching pipeline.  The hint always references the engine
 * evaluation band, game phase, and move quality.
 *
 * Requires X-Api-Key authentication; no Bearer token needed.
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
 */
class HttpLiveMoveClient(
    val baseUrl: String,
    val apiKey: String,
    val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
) : LiveMoveClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = 8_000
        const val DEFAULT_READ_TIMEOUT_MS = 15_000
        private const val LIVE_MOVE_PATH = "/live/move"
    }

    override suspend fun getLiveCoaching(
        fen: String,
        uci: String,
        playerId: String,
    ): ApiResult<LiveMoveResponse> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$baseUrl$LIVE_MOVE_PATH")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("X-Api-Key", apiKey)
            conn.doOutput = true
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            val body = JSONObject()
                .put("fen", fen)
                .put("uci", uci)
                .put("player_id", playerId)
                .toString()
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

    private fun parseResponse(body: String): LiveMoveResponse {
        val root = JSONObject(body)
        val sigObj = root.optJSONObject("engine_signal")
        val engineSignal = sigObj?.let { sig ->
            val evalObj = sig.optJSONObject("evaluation")
            val evaluation = evalObj?.let { ev ->
                EvaluationDto(
                    band = ev.optString("band", "").takeIf { it.isNotEmpty() },
                    side = ev.optString("side", "").takeIf { it.isNotEmpty() },
                )
            }
            EngineSignalDto(
                evaluation = evaluation,
                phase = sig.optString("phase", "").takeIf { it.isNotEmpty() },
            )
        }
        return LiveMoveResponse(
            status = root.optString("status", "ok"),
            hint = root.optString("hint", ""),
            moveQuality = root.optString("move_quality", "unknown"),
            mode = root.optString("mode", "LIVE_V1"),
            engineSignal = engineSignal,
        )
    }
}
