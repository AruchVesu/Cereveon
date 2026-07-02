package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpAuthApiClient.loginWithLichess] against a real
 * local HTTP server.
 *
 * Contract reference: POST /auth/lichess (docs/API_CONTRACTS.md §16a,
 * llm/seca/auth/router.py).  Response is a superset of the /auth/login
 * shape — the extra `created` / `lichess_username` keys must be ignored.
 *
 * Invariants pinned
 * -----------------
 *  1.  INT_LI_METHOD          request method is POST.
 *  2.  INT_LI_PATH            request path is /auth/lichess.
 *  3.  INT_LI_CODE_IN_BODY    code field serialised in request body.
 *  4.  INT_LI_VERIFIER_KEY    code_verifier serialised under its snake_case key.
 *  5.  INT_LI_DEVICE_INFO     device_info field carries "android".
 *  6.  INT_LI_TOKEN_PARSED    access_token deserialised on 200 even with the
 *                             extra response keys present.
 *  7.  INT_LI_HTTP_401        grant rejection → ApiResult.HttpError(401).
 *  8.  INT_LI_HTTP_503        Lichess rate limit → ApiResult.HttpError(503).
 *  9.  INT_LI_TIMEOUT         read timeout → ApiResult.Timeout.
 */
class AuthLichessLoginIntegrationTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start(InetAddress.getByName("127.0.0.1"), 0)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    private fun client(readTimeoutMs: Int = 15_000) =
        HttpAuthApiClient(baseUrl = baseUrl(), readTimeoutMs = readTimeoutMs)

    companion object {
        private const val CODE = "auth-code-abc123"
        private const val VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

        // Deliberately includes the §16a superset fields to prove the
        // shared ApiJson config ignores unknown response keys.
        private const val LICHESS_OK_BODY = """
{
  "access_token": "jwt-lichess-player-token",
  "player_id": "player-lichess-001",
  "token_type": "bearer",
  "created": true,
  "lichess_username": "ChessWizard"
}"""
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 1–2  HTTP method + path
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_LI_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LICHESS_OK_BODY))
        client().loginWithLichess(CODE, VERIFIER)
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_LI_PATH - request path is slash auth slash lichess`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LICHESS_OK_BODY))
        client().loginWithLichess(CODE, VERIFIER)
        assertEquals("/auth/lichess", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3–5  Request body fields
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_LI_CODE_IN_BODY - code serialised in request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LICHESS_OK_BODY))
        client().loginWithLichess(CODE, VERIFIER)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(CODE, body.getString("code"))
    }

    @Test
    fun `INT_LI_VERIFIER_KEY - code_verifier serialised under snake_case key`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LICHESS_OK_BODY))
        client().loginWithLichess(CODE, VERIFIER)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(VERIFIER, body.getString("code_verifier"))
    }

    @Test
    fun `INT_LI_DEVICE_INFO - device_info carries android`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LICHESS_OK_BODY))
        client().loginWithLichess(CODE, VERIFIER)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("android", body.getString("device_info"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 6  Response deserialisation (superset shape)
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_LI_TOKEN_PARSED - access_token parsed despite extra response keys`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LICHESS_OK_BODY))
        val result = client().loginWithLichess(CODE, VERIFIER)
        assertTrue("Expected Success, got: $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LoginResponse
        assertEquals("jwt-lichess-player-token", data.accessToken)
        assertEquals("player-lichess-001", data.playerId)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 7–8  Error mapping
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_LI_HTTP_401 - grant rejection returns HttpError 401`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(401)
                .setBody("""{"detail":"Lichess sign-in failed"}"""),
        )
        val result = client().loginWithLichess(CODE, VERIFIER)
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_LI_HTTP_503 - Lichess rate limit returns HttpError 503`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(503)
                .setBody("""{"detail":"Lichess is busy; try again shortly"}"""),
        )
        val result = client().loginWithLichess(CODE, VERIFIER)
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(503, (result as ApiResult.HttpError).code)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 9  Timeout
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_LI_TIMEOUT - short read timeout returns ApiResult Timeout`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBodyDelay(500, TimeUnit.MILLISECONDS)
                .setBody(LICHESS_OK_BODY),
        )
        val result = client(readTimeoutMs = 1).loginWithLichess(CODE, VERIFIER)
        assertTrue("Expected Timeout, got: $result", result is ApiResult.Timeout)
    }
}
