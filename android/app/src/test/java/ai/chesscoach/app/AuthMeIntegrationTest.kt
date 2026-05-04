package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpAuthApiClient.me] against a real local HTTP server.
 *
 * Contract reference: GET /auth/me (llm/seca/auth/router.py).
 * Auth: Authorization: Bearer <token> required.
 *
 * Invariants pinned
 * -----------------
 *  1.  INT_ME_METHOD        request method is GET.
 *  2.  INT_ME_PATH          request path is /auth/me.
 *  3.  INT_ME_BEARER        Authorization Bearer header is sent with the token.
 *  4.  INT_ME_ID_PARSED     id field deserialised correctly.
 *  5.  INT_ME_EMAIL_PARSED  email field deserialised correctly.
 *  6.  INT_ME_RATING_PARSED rating field deserialised as float.
 *  7.  INT_ME_CONF_PARSED   confidence field deserialised as float.
 *  8.  INT_ME_HTTP_401      401 response → ApiResult.HttpError(401).
 *  9.  INT_ME_TIMEOUT       read timeout → ApiResult.Timeout.
 * 10.  INT_ME_NO_CONTENT_TYPE GET has no Content-Type request header.
 */
class AuthMeIntegrationTest {

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
        private const val ME_OK_BODY = """
{
  "id": "player-abc-123",
  "email": "alice@chess.com",
  "rating": 1482.5,
  "confidence": 0.68
}"""

        private const val ME_WITH_SKILL_VECTOR_BODY = """
{
  "id": "player-abc-123",
  "email": "alice@chess.com",
  "rating": 1482.5,
  "confidence": 0.68,
  "skill_vector": {
    "tactics": 0.72,
    "endgame": 0.45
  }
}"""
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 1  HTTP method
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_METHOD - request uses HTTP GET`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        client().me("test-token")
        assertEquals("GET", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2  Path
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_PATH - request path is slash auth slash me`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        client().me("test-token")
        assertEquals("/auth/me", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3  Auth header
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_BEARER - Authorization Bearer header is sent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        client().me("my-jwt-token")
        val header = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Authorization")
        assertEquals("Bearer my-jwt-token", header)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4–7  Response deserialisation
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_ID_PARSED - id field deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        val result = client().me("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as MeResponse
        assertEquals("player-abc-123", data.id)
    }

    @Test
    fun `INT_ME_EMAIL_PARSED - email field deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        val result = client().me("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as MeResponse
        assertEquals("alice@chess.com", data.email)
    }

    @Test
    fun `INT_ME_RATING_PARSED - rating field deserialised as float`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        val result = client().me("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as MeResponse
        assertEquals(1482.5f, data.rating, 0.01f)
    }

    @Test
    fun `INT_ME_CONF_PARSED - confidence field deserialised as float`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        val result = client().me("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as MeResponse
        assertEquals(0.68f, data.confidence, 0.001f)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 8  401 error
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_HTTP_401 - expired token returns HttpError 401`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Unauthorized"}"""))
        val result = client().me("expired-token")
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 9  Timeout
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_TIMEOUT - short read timeout returns ApiResult Timeout`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBodyDelay(500, TimeUnit.MILLISECONDS)
                .setBody(ME_OK_BODY),
        )
        val result = client(readTimeoutMs = 1).me("tok")
        assertTrue("Expected Timeout, got: $result", result is ApiResult.Timeout)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 10  No Content-Type for GET
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_NO_CONTENT_TYPE - GET request sends no Content-Type header`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        client().me("tok")
        val ct = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Content-Type")
        assertTrue(
            "GET /auth/me must not send Content-Type, was: $ct",
            ct == null || ct.isEmpty(),
        )
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 11–13  skill_vector (P2-A)
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_ME_SKILL_VECTOR_PARSED - skill_vector entries deserialised to map`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_WITH_SKILL_VECTOR_BODY))
        val result = client().me("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as MeResponse
        assertEquals(0.72f, data.skillVector["tactics"] ?: -1f, 0.001f)
        assertEquals(0.45f, data.skillVector["endgame"] ?: -1f, 0.001f)
    }

    @Test
    fun `INT_ME_SKILL_VECTOR_EMPTY - absent skill_vector object yields empty map`() = runBlocking {
        // ME_OK_BODY has no skill_vector field — client must default to emptyMap().
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        val result = client().me("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as MeResponse
        assertTrue(
            "Missing skill_vector field must yield empty map, got: ${data.skillVector}",
            data.skillVector.isEmpty(),
        )
    }

    @Test
    fun `INT_ME_SKILL_VECTOR_MISSING - core fields intact when skill_vector absent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(ME_OK_BODY))
        val result = client().me("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as MeResponse
        assertEquals("player-abc-123", data.id)
        assertEquals(1482.5f, data.rating, 0.01f)
    }
}
