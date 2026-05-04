package ai.chesscoach.app

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Integration tests for the logout flow using [MockWebServer].
 *
 * Invariants pinned
 * -----------------
 *  1. LOGOUT_POST_CALLED_WITH_BEARER:  POST /auth/logout is called with Authorization: Bearer <token>.
 *  2. TOKEN_CLEARED_ON_SUCCESS:        Token is cleared from AuthRepository after HTTP 200.
 *  3. TOKEN_CLEARED_ON_HTTP_ERROR:     Token is cleared even when the server returns HTTP 500.
 *  4. TOKEN_CLEARED_ON_NETWORK_ERROR:  Token is cleared even when the server is unreachable.
 *  5. LOGOUT_PATH_CORRECT:             Request path is /auth/logout.
 *  6. LOGOUT_METHOD_POST:              Request method is POST.
 *  7. NO_LOGOUT_CALL_WHEN_TOKEN_NULL:  performLogout skips network call when no token is stored.
 */
class AuthLogoutIntegrationTest {

    private lateinit var server: MockWebServer

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    /**
     * In-memory [TokenStorage] for JVM tests (avoids Android Keystore dependency).
     */
    private class MemoryTokenStorage(initialToken: String? = null) : TokenStorage {
        private var token: String? = initialToken
        override fun save(t: String) { token = t }
        override fun load(): String? = token
        override fun clear() { token = null }
    }

    /**
     * Pure-Kotlin implementation of the MainActivity logout sequence,
     * extracted for testability: call logout → clearToken regardless of result.
     */
    private suspend fun performLogout(
        token: String?,
        authClient: AuthApiClient,
        authRepo: AuthRepository,
    ) {
        if (token != null) {
            authClient.logout(token)   // best-effort; ignore result
        }
        authRepo.clearToken()
    }

    // ------------------------------------------------------------------
    // 1. Logout POST includes Authorization: Bearer header
    // ------------------------------------------------------------------

    @Test
    fun `logout POST includes Authorization Bearer header`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"logged_out"}"""))

        val client = HttpAuthApiClient(baseUrl = baseUrl())
        val repo = AuthRepository(MemoryTokenStorage("my-jwt-token"))
        performLogout("my-jwt-token", client, repo)

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer my-jwt-token", req.getHeader("Authorization"))
    }

    // ------------------------------------------------------------------
    // 2. Token cleared after HTTP 200
    // ------------------------------------------------------------------

    @Test
    fun `token is cleared from AuthRepository after successful logout`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"logged_out"}"""))

        val storage = MemoryTokenStorage("valid-token")
        val repo = AuthRepository(storage)
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        performLogout("valid-token", client, repo)

        assertNull("Token must be cleared after logout", repo.getToken())
    }

    // ------------------------------------------------------------------
    // 3. Token cleared even on HTTP 500
    // ------------------------------------------------------------------

    @Test
    fun `token is cleared even when server returns HTTP 500`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500))

        val storage = MemoryTokenStorage("my-token")
        val repo = AuthRepository(storage)
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        performLogout("my-token", client, repo)

        assertNull("Token must be cleared regardless of server error", repo.getToken())
    }

    // ------------------------------------------------------------------
    // 4. Token cleared when server is unreachable (network error)
    // ------------------------------------------------------------------

    @Test
    fun `token is cleared even when server is unreachable`() = runBlocking {
        // Use a port that is not listening to trigger a network error
        val client = HttpAuthApiClient(
            baseUrl = "http://127.0.0.1:1",
            connectTimeoutMs = 500,
            readTimeoutMs = 500,
        )
        val storage = MemoryTokenStorage("my-token")
        val repo = AuthRepository(storage)

        performLogout("my-token", client, repo)

        assertNull("Token must be cleared even on network failure", repo.getToken())
    }

    // ------------------------------------------------------------------
    // 5. Request path is /auth/logout
    // ------------------------------------------------------------------

    @Test
    fun `logout request uses the correct path`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"logged_out"}"""))

        val client = HttpAuthApiClient(baseUrl = baseUrl())
        val repo = AuthRepository(MemoryTokenStorage("tok"))
        performLogout("tok", client, repo)

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("/auth/logout", req.path)
    }

    // ------------------------------------------------------------------
    // 6. Request method is POST
    // ------------------------------------------------------------------

    @Test
    fun `logout request method is POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"logged_out"}"""))

        val client = HttpAuthApiClient(baseUrl = baseUrl())
        val repo = AuthRepository(MemoryTokenStorage("tok"))
        performLogout("tok", client, repo)

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", req.method)
    }

    // ------------------------------------------------------------------
    // 7. No network call when token is null
    // ------------------------------------------------------------------

    @Test
    fun `no logout network call is made when token is null`() = runBlocking {
        val client = HttpAuthApiClient(baseUrl = baseUrl())
        val repo = AuthRepository(MemoryTokenStorage(null))

        performLogout(null, client, repo)

        // No request should have been dispatched
        assertEquals(0, server.requestCount)
        assertNull(repo.getToken())
    }
}
