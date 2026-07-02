package ai.chesscoach.app

import kotlinx.serialization.encodeToString
import java.net.HttpURLConnection

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
     * POST /auth/lichess — "Sign in with Lichess" (OAuth PKCE).
     *
     * Forwards the one-time authorization [code] from the Lichess
     * redirect plus the PKCE [codeVerifier] that [LichessOAuth] minted
     * for this attempt; the backend performs the code exchange and
     * returns the same token shape as [login] (`docs/API_CONTRACTS.md`
     * §16a).  Transparently creates the account on first sign-in.
     *
     * Default implementation returns [ApiResult.HttpError(501)] so test
     * fakes do not need to override this method.
     *
     * @return [ApiResult.Success] with [LoginResponse] on HTTP 200;
     *         [ApiResult.HttpError(401)] when Lichess rejected the grant
     *         (restart the authorization flow); (502)/(503) on Lichess
     *         upstream / rate-limit; transport variants otherwise.
     */
    suspend fun loginWithLichess(
        code: String,
        codeVerifier: String,
    ): ApiResult<LoginResponse> = ApiResult.HttpError(501)

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
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
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
        const val DEFAULT_CONNECT_TIMEOUT_MS = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS
        const val DEFAULT_READ_TIMEOUT_MS = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS
        private const val LOGIN_PATH = "/auth/login"
        private const val LICHESS_LOGIN_PATH = "/auth/lichess"
        private const val LOGOUT_PATH = "/auth/logout"
        private const val ME_PATH = "/auth/me"
        private const val REGISTER_PATH = "/auth/register"
        private const val CHANGE_PASSWORD_PATH = "/auth/change-password"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    private fun bearerHeader(token: String): Map<String, String> =
        mapOf("Authorization" to "Bearer $token")

    private fun refreshOnSuccess(): (java.net.HttpURLConnection) -> Unit =
        { conn -> consumeRefreshedToken(conn, tokenSink) }

    override suspend fun login(
        email: String,
        password: String,
    ): ApiResult<LoginResponse> = http.request(
        path = LOGIN_PATH,
        method = "POST",
        body = ApiJson.encodeToString(
            LoginRequest(email = email, password = password, deviceInfo = "android")
        ),
        parse = { body -> ApiJson.decodeFromString<LoginResponse>(body) },
    )

    override suspend fun loginWithLichess(
        code: String,
        codeVerifier: String,
    ): ApiResult<LoginResponse> = http.request(
        path = LICHESS_LOGIN_PATH,
        method = "POST",
        body = ApiJson.encodeToString(
            LichessLoginRequest(code = code, codeVerifier = codeVerifier, deviceInfo = "android")
        ),
        parse = { body -> ApiJson.decodeFromString<LoginResponse>(body) },
    )

    override suspend fun logout(token: String): ApiResult<Unit> = http.requestNoBody(
        path = LOGOUT_PATH,
        method = "POST",
        headers = bearerHeader(token),
    )

    override suspend fun me(token: String): ApiResult<MeResponse> = http.request(
        path = ME_PATH,
        method = "GET",
        headers = bearerHeader(token),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<MeResponse>(body) },
    )

    override suspend fun updateMe(
        token: String,
        rating: Float?,
        confidence: Float?,
    ): ApiResult<MeResponse> = http.request(
        // POST + X-HTTP-Method-Override: PATCH — the JDK's HttpURLConnection
        // rejects PATCH as a request method on JDK 17 (the host JVM tests
        // target), but Android's OkHttp-backed implementation accepts it.
        // Going via the override header keeps a single code path that
        // works on both runtimes; the backend strips the header in the
        // http_method_override middleware in server.py and routes it
        // as a real PATCH /auth/me.
        path = ME_PATH,
        method = "POST",
        headers = bearerHeader(token) + ("X-HTTP-Method-Override" to "PATCH"),
        // ``ApiJson.encodeDefaults = false`` strips null fields from the
        // wire payload so the server-side validators (rating: float | None,
        // confidence: float | None) get exactly the keys the client
        // intended to update.  Sending {} (both null) produces a 400 from
        // the backend — preserved so a malformed call surfaces
        // immediately rather than appearing as a no-op success.
        body = ApiJson.encodeToString(
            UpdateMeRequest(rating = rating, confidence = confidence)
        ),
        onResponse = refreshOnSuccess(),
        parse = { body -> ApiJson.decodeFromString<MeResponse>(body) },
    )

    override suspend fun changePassword(
        currentPassword: String,
        newPassword: String,
        token: String,
    ): ApiResult<Unit> = http.requestNoBody(
        path = CHANGE_PASSWORD_PATH,
        method = "POST",
        headers = bearerHeader(token),
        body = ApiJson.encodeToString(
            ChangePasswordRequest(
                currentPassword = currentPassword,
                newPassword = newPassword,
            )
        ),
        onResponse = refreshOnSuccess(),
    )

    override suspend fun register(
        email: String,
        password: String,
    ): ApiResult<LoginResponse> = http.request(
        path = REGISTER_PATH,
        method = "POST",
        body = ApiJson.encodeToString(
            RegisterRequest(email = email, password = password, deviceInfo = "android")
        ),
        // Register is the only endpoint that returns 201 Created in
        // addition to 200 OK; widen the success set accordingly.
        successCodes = setOf(HttpURLConnection.HTTP_OK, HttpURLConnection.HTTP_CREATED),
        parse = { body -> ApiJson.decodeFromString<LoginResponse>(body) },
    )
}
