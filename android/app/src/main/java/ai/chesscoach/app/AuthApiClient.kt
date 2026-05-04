package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/**
 * Shared client interface for the backend authentication endpoints.
 *
 * Returns [ApiResult] on every call — callers never see raw exceptions.
 * Implementations are safe to call from any coroutine context.
 */
interface AuthApiClient {

    /**
     * POST /auth/login.
     *
     * @return [ApiResult.Success] with [LoginResponse] on HTTP 200.
     *         [ApiResult.HttpError(401)] for invalid credentials.
     *         [ApiResult.Timeout] or [ApiResult.NetworkError] on transport failures.
     */
    suspend fun login(email: String, password: String): ApiResult<LoginResponse>

    /**
     * POST /auth/logout.
     *
     * Sends [token] in the Authorization header; invalidates the server-side
     * session so the token can no longer be used.
     *
     * @return [ApiResult.Success(Unit)] on HTTP 200; error variants otherwise.
     */
    suspend fun logout(token: String): ApiResult<Unit>

    /**
     * GET /auth/me.
     *
     * Returns the authenticated player's profile (id, email, rating, confidence).
     * Called at cold-start so the rating header is populated before the first game.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so that test
     * fakes that only override login/logout do not need to implement this method.
     *
     * @return [ApiResult.Success] with [MeResponse] on HTTP 200;
     *         [ApiResult.HttpError(401)] when token is invalid or expired;
     *         [ApiResult.Timeout] or [ApiResult.NetworkError] on transport failures.
     */
    suspend fun me(token: String): ApiResult<MeResponse> = ApiResult.HttpError(501)

    /**
     * POST /auth/register.
     *
     * Creates a new player account.  Returns the same shape as [login] on
     * success (HTTP 200/201) so the caller can immediately save the token
     * and navigate to [MainActivity].
     *
     * Default implementation returns [ApiResult.HttpError(501)] so that test
     * fakes do not need to override this method.
     *
     * @return [ApiResult.Success] with [LoginResponse] on success;
     *         [ApiResult.HttpError(409)] when email is already registered;
     *         [ApiResult.Timeout] or [ApiResult.NetworkError] on transport failures.
     */
    suspend fun register(email: String, password: String): ApiResult<LoginResponse> =
        ApiResult.HttpError(501)

    /**
     * POST /auth/change-password.
     *
     * Requires a valid [token] (Bearer). Returns [ApiResult.HttpError(400)] when
     * [currentPassword] does not match the stored hash.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test fakes do
     * not need to override this method.
     */
    suspend fun changePassword(
        currentPassword: String,
        newPassword: String,
        token: String,
    ): ApiResult<Unit> = ApiResult.HttpError(501)

    /**
     * PATCH /auth/me — partial profile update.
     *
     * Forwards the calibration estimate produced by the Onboarding
     * screen so the server's adaptation layer can dispatch the first
     * opponent at the right level instead of waiting for rating drift
     * to converge.
     *
     * At least one of [rating] / [confidence] must be non-null, otherwise
     * the backend returns 400.  Both null is allowed at the call site
     * (callers can compose the request from optional fields without
     * pre-filtering); the backend is the source of truth for the bound
     * checks (rating in (0, 4000], confidence in [0, 1]).
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test
     * fakes that only override login/logout do not need to implement
     * this method.
     *
     * @return [ApiResult.Success] with the *post-update* [MeResponse]
     *         on HTTP 200 — same shape as [me] so the client can
     *         replace its cache from this single round-trip.
     *         [ApiResult.HttpError(400)] on out-of-bounds values or
     *         empty payload; [ApiResult.HttpError(401)] on invalid
     *         token; transport variants otherwise.
     */
    suspend fun updateMe(
        token: String,
        rating: Float? = null,
        confidence: Float? = null,
    ): ApiResult<MeResponse> = ApiResult.HttpError(501)
}

/**
 * Production [AuthApiClient] backed by [HttpURLConnection].
 *
 * All I/O is dispatched to [Dispatchers.IO] — safe to call from any coroutine.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash
 *                         (e.g. "http://10.0.2.2:8000").
 * @param connectTimeoutMs TCP connect deadline in milliseconds.
 * @param readTimeoutMs    Read deadline in milliseconds.
 */
class HttpAuthApiClient(
    val baseUrl: String,
    val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
    /**
     * Optional sink for the X-Auth-Token refresh header — see
     * [TokenRefresh].  When provided, every successful authenticated
     * response (currently [me], [updateMe], [changePassword]) hands
     * the freshly-minted JWT to this lambda so callers can rotate
     * their stored token transparently.  Null disables the rotation
     * (test fakes / clients that don't store tokens).
     */
    val tokenSink: ((String) -> Unit)? = null,
) : AuthApiClient {

    companion object {
        const val DEFAULT_CONNECT_TIMEOUT_MS = 8_000
        const val DEFAULT_READ_TIMEOUT_MS = 15_000
        private const val LOGIN_PATH = "/auth/login"
        private const val LOGOUT_PATH = "/auth/logout"
        private const val ME_PATH = "/auth/me"
        private const val REGISTER_PATH = "/auth/register"
        private const val CHANGE_PASSWORD_PATH = "/auth/change-password"
    }

    override suspend fun login(
        email: String,
        password: String,
    ): ApiResult<LoginResponse> = withContext(Dispatchers.IO) {
        try {
            val body =
                JSONObject().apply {
                    put("email", email)
                    put("password", password)
                    put("device_info", "android")
                }.toString()

            val url = URL("$baseUrl$LOGIN_PATH")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json")
            conn.doOutput = true
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            conn.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(body) }

            val code = conn.responseCode
            if (code == HttpURLConnection.HTTP_OK) {
                val raw = conn.inputStream.bufferedReader(Charsets.UTF_8).readText()
                ApiResult.Success(parseLoginResponse(raw))
            } else {
                ApiResult.HttpError(code)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    override suspend fun logout(
        token: String,
    ): ApiResult<Unit> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$baseUrl$LOGOUT_PATH")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            val code = conn.responseCode
            if (code == HttpURLConnection.HTTP_OK) {
                ApiResult.Success(Unit)
            } else {
                ApiResult.HttpError(code)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    override suspend fun me(
        token: String,
    ): ApiResult<MeResponse> = withContext(Dispatchers.IO) {
        try {
            val url = URL("$baseUrl$ME_PATH")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "GET"
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            val code = conn.responseCode
            if (code == HttpURLConnection.HTTP_OK) {
                val raw = conn.inputStream.bufferedReader(Charsets.UTF_8).readText()
                consumeRefreshedToken(conn, tokenSink)
                ApiResult.Success(parseMeResponse(raw))
            } else {
                ApiResult.HttpError(code)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    override suspend fun updateMe(
        token: String,
        rating: Float?,
        confidence: Float?,
    ): ApiResult<MeResponse> = withContext(Dispatchers.IO) {
        try {
            // Build a body with only the non-null fields.  Sending {}
            // (both null) produces a 400 from the backend — preserved
            // so a malformed call surfaces immediately rather than
            // appearing as a no-op success.
            val body = JSONObject().apply {
                if (rating != null) put("rating", rating.toDouble())
                if (confidence != null) put("confidence", confidence.toDouble())
            }.toString()

            val url = URL("$baseUrl$ME_PATH")
            val conn = url.openConnection() as HttpURLConnection
            // POST + X-HTTP-Method-Override: PATCH — the JDK's
            // HttpURLConnection rejects PATCH as a request method on
            // JDK 17 (the host JVM tests target), but Android's
            // OkHttp-backed implementation accepts it.  Going via the
            // override header keeps a single code path that works on
            // both runtimes; the backend strips the header in the
            // http_method_override middleware in server.py and routes
            // it as a real PATCH /auth/me.
            conn.requestMethod = "POST"
            conn.setRequestProperty("X-HTTP-Method-Override", "PATCH")
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.doOutput = true
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            conn.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(body) }

            val code = conn.responseCode
            if (code == HttpURLConnection.HTTP_OK) {
                val raw = conn.inputStream.bufferedReader(Charsets.UTF_8).readText()
                consumeRefreshedToken(conn, tokenSink)
                ApiResult.Success(parseMeResponse(raw))
            } else {
                ApiResult.HttpError(code)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    override suspend fun changePassword(
        currentPassword: String,
        newPassword: String,
        token: String,
    ): ApiResult<Unit> = withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().apply {
                put("current_password", currentPassword)
                put("new_password", newPassword)
            }.toString()
            val url = URL("$baseUrl$CHANGE_PASSWORD_PATH")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.doOutput = true
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs
            conn.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(body) }
            val code = conn.responseCode
            if (code == HttpURLConnection.HTTP_OK) {
                consumeRefreshedToken(conn, tokenSink)
                ApiResult.Success(Unit)
            } else {
                ApiResult.HttpError(code)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    private fun parseLoginResponse(body: String): LoginResponse {
        val root = JSONObject(body)
        return LoginResponse(
            accessToken = root.getString("access_token"),
            playerId = root.optString("player_id", ""),
            tokenType = root.optString("token_type", "bearer"),
        )
    }

    override suspend fun register(
        email: String,
        password: String,
    ): ApiResult<LoginResponse> = withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().apply {
                put("email", email)
                put("password", password)
                put("device_info", "android")
            }.toString()

            val url = URL("$baseUrl$REGISTER_PATH")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json")
            conn.doOutput = true
            conn.connectTimeout = connectTimeoutMs
            conn.readTimeout = readTimeoutMs

            conn.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(body) }

            val code = conn.responseCode
            if (code == HttpURLConnection.HTTP_OK || code == HttpURLConnection.HTTP_CREATED) {
                val raw = conn.inputStream.bufferedReader(Charsets.UTF_8).readText()
                ApiResult.Success(parseLoginResponse(raw))
            } else {
                ApiResult.HttpError(code)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult.Timeout
        } catch (e: Exception) {
            ApiResult.NetworkError(e)
        }
    }

    private fun parseMeResponse(body: String): MeResponse {
        val root = JSONObject(body)
        val svJson = root.optJSONObject("skill_vector") ?: JSONObject()
        val skillVector = buildMap<String, Float> {
            svJson.keys().forEach { key -> put(key, svJson.optDouble(key, 0.0).toFloat()) }
        }
        return MeResponse(
            id = root.optString("id", ""),
            email = root.optString("email", ""),
            rating = root.optDouble("rating", 0.0).toFloat(),
            confidence = root.optDouble("confidence", 0.0).toFloat(),
            skillVector = skillVector,
        )
    }
}
