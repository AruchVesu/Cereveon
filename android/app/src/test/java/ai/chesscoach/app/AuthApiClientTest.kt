package ai.chesscoach.app

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the authentication client abstraction layer.
 *
 * Uses a [FakeAuthApiClient] test double — [HttpAuthApiClient] network I/O
 * is not tested here and requires integration / instrumented tests against
 * a live or test-double server.
 *
 * Invariants pinned
 * -----------------
 *  1.  LOGIN_RESPONSE_DATA:          LoginResponse data is accessible on Success.
 *  2.  LOGIN_HTTP_ERROR_CODE:        HttpError stores the status code.
 *  3.  LOGIN_NETWORK_CAUSE:          NetworkError stores the exception.
 *  4.  LOGIN_TIMEOUT:                Timeout result is returned correctly.
 *  5.  LOGOUT_SUCCESS:               Successful logout returns Success(Unit).
 *  6.  LOGOUT_HTTP_ERROR:            Logout HttpError stores the code.
 *  7.  LOGOUT_NETWORK_ERROR:         Logout NetworkError stores the cause.
 *  8.  FAKE_RECORDS_LOGIN_EMAIL:     FakeAuthApiClient records last email.
 *  9.  FAKE_RECORDS_LOGIN_PASSWORD:  FakeAuthApiClient records last password.
 * 10.  FAKE_RECORDS_LOGOUT_TOKEN:    FakeAuthApiClient records last logout token.
 * 11.  FAKE_LOGIN_CALL_COUNT:        FakeAuthApiClient counts login calls.
 * 12.  FAKE_LOGOUT_CALL_COUNT:       FakeAuthApiClient counts logout calls.
 * 13.  CONTRACT_REPLY_ON_SUCCESS:    Caller extracts accessToken from Success.
 * 14.  CONTRACT_EMPTY_ON_401:        Caller extracts "" for HttpError(401).
 * 15.  CONTRACT_EMPTY_ON_TIMEOUT:    Caller extracts "" for Timeout.
 * 16.  HTTP_AUTH_CLIENT_DEFAULTS:    HttpAuthApiClient has correct default timeouts.
 * 17.  HTTP_AUTH_CLIENT_BASE_URL:    HttpAuthApiClient stores baseUrl.
 * 18.  MULTI_LOGIN_LAST_WINS:        FakeAuthApiClient retains last-call state.
 * 19.  RESULT_PATTERN_ALL_BRANCHES:  when() correctly matches all four ApiResult branches.
 * 20.  LOGIN_RESPONSE_EQUALITY:      Two identical LoginResponse objects are equal.
 */
class AuthApiClientTest {

    // ------------------------------------------------------------------
    // Test double
    // ------------------------------------------------------------------

    /**
     * Fake [AuthApiClient] for unit-testing callers of the interface.
     *
     * [nextLoginResult] and [nextLogoutResult] are returned by their respective
     * methods. Introspection fields ([loginCallCount], [lastEmail], etc.) allow
     * assertions on how the client was invoked.
     */
    private class FakeAuthApiClient(
        var nextLoginResult: ApiResult<LoginResponse> =
            ApiResult.Success(LoginResponse("tok", "pid", "bearer")),
        var nextLogoutResult: ApiResult<Unit> = ApiResult.Success(Unit),
    ) : AuthApiClient {

        var loginCallCount = 0
        var logoutCallCount = 0
        var lastEmail: String? = null
        var lastPassword: String? = null
        var lastLogoutToken: String? = null

        override suspend fun login(email: String, password: String): ApiResult<LoginResponse> {
            loginCallCount++
            lastEmail = email
            lastPassword = password
            return nextLoginResult
        }

        override suspend fun logout(token: String): ApiResult<Unit> {
            logoutCallCount++
            lastLogoutToken = token
            return nextLogoutResult
        }
    }

    // ------------------------------------------------------------------
    // 1–4  Login ApiResult variants
    // ------------------------------------------------------------------

    @Test
    fun `login Success contains the LoginResponse data`() =
        runBlocking {
            val expected = LoginResponse("my-token", "player-id", "bearer")
            val fake = FakeAuthApiClient(nextLoginResult = ApiResult.Success(expected))
            val result = fake.login("user@test.com", "pass")
            assertTrue(result is ApiResult.Success)
            assertEquals(expected, (result as ApiResult.Success).data)
        }

    @Test
    fun `login HttpError stores the status code`() =
        runBlocking {
            val fake = FakeAuthApiClient(nextLoginResult = ApiResult.HttpError(401))
            val result = fake.login("x@y.com", "wrong")
            assertTrue(result is ApiResult.HttpError)
            assertEquals(401, (result as ApiResult.HttpError).code)
        }

    @Test
    fun `login NetworkError stores the exception`() =
        runBlocking {
            val cause = RuntimeException("Connection refused")
            val fake = FakeAuthApiClient(nextLoginResult = ApiResult.NetworkError(cause))
            val result = fake.login("x@y.com", "p")
            assertTrue(result is ApiResult.NetworkError)
            assertSame(cause, (result as ApiResult.NetworkError).cause)
        }

    @Test
    fun `login Timeout is returned correctly`() =
        runBlocking {
            val fake = FakeAuthApiClient(nextLoginResult = ApiResult.Timeout)
            val result = fake.login("x@y.com", "p")
            assertSame(ApiResult.Timeout, result)
        }

    // ------------------------------------------------------------------
    // 5–7  Logout ApiResult variants
    // ------------------------------------------------------------------

    @Test
    fun `logout Success returns Unit`() =
        runBlocking {
            val fake = FakeAuthApiClient(nextLogoutResult = ApiResult.Success(Unit))
            val result = fake.logout("my-token")
            assertTrue(result is ApiResult.Success)
        }

    @Test
    fun `logout HttpError stores the status code`() =
        runBlocking {
            val fake = FakeAuthApiClient(nextLogoutResult = ApiResult.HttpError(403))
            val result = fake.logout("bad-token")
            assertTrue(result is ApiResult.HttpError)
            assertEquals(403, (result as ApiResult.HttpError).code)
        }

    @Test
    fun `logout NetworkError stores the exception cause`() =
        runBlocking {
            val cause = RuntimeException("No host")
            val fake = FakeAuthApiClient(nextLogoutResult = ApiResult.NetworkError(cause))
            val result = fake.logout("tok")
            assertTrue(result is ApiResult.NetworkError)
            assertSame(cause, (result as ApiResult.NetworkError).cause)
        }

    // ------------------------------------------------------------------
    // 8–12  FakeAuthApiClient introspection
    // ------------------------------------------------------------------

    @Test
    fun `FakeAuthApiClient records the last email used for login`() =
        runBlocking {
            val fake = FakeAuthApiClient()
            fake.login("user@chess.com", "pass")
            assertEquals("user@chess.com", fake.lastEmail)
        }

    @Test
    fun `FakeAuthApiClient records the last password used for login`() =
        runBlocking {
            val fake = FakeAuthApiClient()
            fake.login("user@chess.com", "secret123")
            assertEquals("secret123", fake.lastPassword)
        }

    @Test
    fun `FakeAuthApiClient records the token used for logout`() =
        runBlocking {
            val fake = FakeAuthApiClient()
            fake.logout("logout-token-xyz")
            assertEquals("logout-token-xyz", fake.lastLogoutToken)
        }

    @Test
    fun `FakeAuthApiClient counts login calls correctly`() =
        runBlocking {
            val fake = FakeAuthApiClient()
            fake.login("a@b.com", "p1")
            fake.login("a@b.com", "p2")
            assertEquals(2, fake.loginCallCount)
        }

    @Test
    fun `FakeAuthApiClient counts logout calls correctly`() =
        runBlocking {
            val fake = FakeAuthApiClient()
            fake.logout("t1")
            fake.logout("t2")
            fake.logout("t3")
            assertEquals(3, fake.logoutCallCount)
        }

    // ------------------------------------------------------------------
    // 13–15  Interface contract — caller when-branch behaviour
    // ------------------------------------------------------------------

    @Test
    fun `calling login on Success yields the accessToken`() =
        runBlocking {
            val fake =
                FakeAuthApiClient(
                    nextLoginResult = ApiResult.Success(LoginResponse("jwt-token", "pid", "bearer")),
                )
            val result = fake.login("u@v.com", "pw")
            val token =
                when (result) {
                    is ApiResult.Success -> result.data.accessToken
                    is ApiResult.HttpError, is ApiResult.NetworkError, ApiResult.Timeout -> ""
                }
            assertEquals("jwt-token", token)
        }

    @Test
    fun `login HttpError 401 produces empty token via when branch`() =
        runBlocking {
            val fake = FakeAuthApiClient(nextLoginResult = ApiResult.HttpError(401))
            val result = fake.login("u@v.com", "bad")
            val token =
                when (result) {
                    is ApiResult.Success -> result.data.accessToken
                    is ApiResult.HttpError, is ApiResult.NetworkError, ApiResult.Timeout -> ""
                }
            assertEquals("", token)
        }

    @Test
    fun `login Timeout produces empty token via when branch`() =
        runBlocking {
            val fake = FakeAuthApiClient(nextLoginResult = ApiResult.Timeout)
            val result = fake.login("u@v.com", "pw")
            val token =
                when (result) {
                    is ApiResult.Success -> result.data.accessToken
                    is ApiResult.HttpError, is ApiResult.NetworkError, ApiResult.Timeout -> ""
                }
            assertEquals("", token)
        }

    // ------------------------------------------------------------------
    // 16–17  HttpAuthApiClient constructor properties
    // ------------------------------------------------------------------

    @Test
    fun `HttpAuthApiClient has correct default timeout values`() {
        val client = HttpAuthApiClient(baseUrl = "http://localhost")
        assertEquals(HttpAuthApiClient.DEFAULT_CONNECT_TIMEOUT_MS, client.connectTimeoutMs)
        assertEquals(HttpAuthApiClient.DEFAULT_READ_TIMEOUT_MS, client.readTimeoutMs)
    }

    @Test
    fun `HttpAuthApiClient stores the baseUrl`() {
        val client = HttpAuthApiClient(baseUrl = "http://10.0.2.2:8000")
        assertEquals("http://10.0.2.2:8000", client.baseUrl)
    }

    // ------------------------------------------------------------------
    // 18  Multi-call state
    // ------------------------------------------------------------------

    @Test
    fun `FakeAuthApiClient retains state from the last login call`() =
        runBlocking {
            val fake = FakeAuthApiClient()
            fake.login("first@test.com", "p1")
            fake.login("second@test.com", "p2")
            assertEquals("second@test.com", fake.lastEmail)
            assertEquals("p2", fake.lastPassword)
        }

    // ------------------------------------------------------------------
    // 19  Pattern matching across all ApiResult branches
    // ------------------------------------------------------------------

    @Test
    fun `when expression matches all four ApiResult branches for login`() {
        val results: List<ApiResult<LoginResponse>> =
            listOf(
                ApiResult.Success(LoginResponse("t", "p", "bearer")),
                ApiResult.HttpError(401),
                ApiResult.NetworkError(RuntimeException("err")),
                ApiResult.Timeout,
            )
        val kinds =
            results.map { r ->
                when (r) {
                    is ApiResult.Success -> "success"
                    is ApiResult.HttpError -> "http"
                    is ApiResult.NetworkError -> "network"
                    ApiResult.Timeout -> "timeout"
                }
            }
        assertEquals(listOf("success", "http", "network", "timeout"), kinds)
    }

    // ------------------------------------------------------------------
    // 20  LoginResponse equality
    // ------------------------------------------------------------------

    @Test
    fun `two identical LoginResponse objects are equal`() {
        val a = LoginResponse("tok", "pid", "bearer")
        val b = LoginResponse("tok", "pid", "bearer")
        assertEquals(a, b)
    }
}
