package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpAuthApiClient.updateMe] against a real local HTTP server.
 *
 * Contract reference: PATCH /auth/me (llm/seca/auth/router.py).
 * Auth: Authorization: Bearer <token> required.
 * Body: {"rating": <float>?, "confidence": <float>?} — at least one field.
 *
 * Wire semantics: the client sends POST + ``X-HTTP-Method-Override:
 * PATCH`` (the JDK's HttpURLConnection rejects PATCH as a request
 * method on JDK 17).  The backend strips the header and routes it as
 * a real PATCH — see ``http_method_override`` middleware in server.py
 * and ``test_auth_update_me_method_override`` for the server-side
 * contract.  The assertions below pin the *wire* shape; the
 * server-side translation is verified separately.
 *
 * Invariants pinned
 * -----------------
 *  1.  INT_PATCH_METHOD             wire method is POST + override header.
 *  2.  INT_PATCH_OVERRIDE_HEADER    X-HTTP-Method-Override: PATCH is sent.
 *  3.  INT_PATCH_PATH               request path is /auth/me.
 *  4.  INT_PATCH_BEARER             Authorization Bearer header is sent.
 *  5.  INT_PATCH_CONTENT_TYPE       Content-Type: application/json.
 *  6.  INT_PATCH_BOTH_FIELDS_BODY   body contains both rating + confidence
 *                                   when both are non-null.
 *  7.  INT_PATCH_RATING_ONLY_BODY   body contains only rating when
 *                                   confidence is null (no `confidence` key).
 *  8.  INT_PATCH_CONFIDENCE_ONLY_BODY body contains only confidence when
 *                                   rating is null (no `rating` key).
 *  9.  INT_PATCH_RESPONSE_PARSED    200 OK response parses into MeResponse.
 * 10.  INT_PATCH_400_HTTP_ERROR     400 (out-of-bounds) → ApiResult.HttpError(400).
 * 11.  INT_PATCH_401_HTTP_ERROR     401 (bad token) → ApiResult.HttpError(401).
 * 12.  INT_PATCH_TIMEOUT            read timeout → ApiResult.Timeout.
 */
class AuthMeUpdateIntegrationTest {

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
        private const val PATCH_OK_BODY = """
{
  "id": "player-abc-123",
  "email": "alice@chess.com",
  "rating": 1720.0,
  "confidence": 0.85,
  "skill_vector": {}
}"""
        private const val TOKEN = "test-jwt-token"
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 1  HTTP method + path + auth
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_PATCH_METHOD - wire method is POST (HTTP override pattern)`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = 1720f, confidence = 0.85f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", req.method)
    }

    @Test
    fun `INT_PATCH_OVERRIDE_HEADER - X-HTTP-Method-Override is PATCH`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = 1720f, confidence = 0.85f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("PATCH", req.getHeader("X-HTTP-Method-Override"))
    }

    @Test
    fun `INT_PATCH_PATH - request hits auth me`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = 1720f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("/auth/me", req.path)
    }

    @Test
    fun `INT_PATCH_BEARER - Authorization Bearer header is sent`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = 1720f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer $TOKEN", req.getHeader("Authorization"))
    }

    @Test
    fun `INT_PATCH_CONTENT_TYPE - Content-Type is application json`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = 1720f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("application/json", req.getHeader("Content-Type"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2  Body shape
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_PATCH_BOTH_FIELDS_BODY - both fields included when non-null`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = 1720f, confidence = 0.85f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        val body = JSONObject(req.body.readUtf8())
        assertTrue("body must contain rating",     body.has("rating"))
        assertTrue("body must contain confidence", body.has("confidence"))
        assertEquals(1720.0, body.getDouble("rating"),     1e-3)
        assertEquals(0.85,   body.getDouble("confidence"), 1e-6)
    }

    @Test
    fun `INT_PATCH_RATING_ONLY_BODY - omits confidence key when null`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = 1500f, confidence = null)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        val body = JSONObject(req.body.readUtf8())
        assertTrue("body must contain rating", body.has("rating"))
        assertFalse(
            "body must NOT contain confidence when caller passed null — " +
                "otherwise PATCH would zero out the existing server value",
            body.has("confidence"),
        )
    }

    @Test
    fun `INT_PATCH_CONFIDENCE_ONLY_BODY - omits rating key when null`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        client().updateMe(TOKEN, rating = null, confidence = 0.5f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        val body = JSONObject(req.body.readUtf8())
        assertTrue("body must contain confidence", body.has("confidence"))
        assertFalse(
            "body must NOT contain rating when caller passed null — " +
                "otherwise PATCH would zero out the existing server value",
            body.has("rating"),
        )
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3  Response parsing
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_PATCH_RESPONSE_PARSED - 200 OK parses into MeResponse`() = runBlocking {
        server.enqueue(MockResponse().setBody(PATCH_OK_BODY).setResponseCode(200))
        val result = client().updateMe(TOKEN, rating = 1720f, confidence = 0.85f)
        assertTrue("expected success, got $result", result is ApiResult.Success)
        val me = (result as ApiResult.Success).data
        assertEquals("player-abc-123", me.id)
        assertEquals("alice@chess.com", me.email)
        assertEquals(1720f, me.rating)
        assertEquals(0.85f, me.confidence)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4  Error responses
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_PATCH_400_HTTP_ERROR - 400 maps to HttpError(400)`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"detail":"rating must be in (0, 4000]"}"""))
        val result = client().updateMe(TOKEN, rating = 9999f)
        assertTrue("expected HttpError(400), got $result", result is ApiResult.HttpError)
        assertEquals(400, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_PATCH_401_HTTP_ERROR - 401 maps to HttpError(401)`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Invalid token"}"""))
        val result = client().updateMe("bad-token", rating = 1500f)
        assertTrue("expected HttpError(401), got $result", result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_PATCH_TIMEOUT - read timeout maps to ApiResult Timeout`() = runBlocking {
        // Use a 200ms read timeout and a server response delayed 2s so
        // the read deadline is hit before the body arrives.
        server.enqueue(
            MockResponse()
                .setBody(PATCH_OK_BODY)
                .setBodyDelay(2, TimeUnit.SECONDS)
                .setResponseCode(200),
        )
        val result = client(readTimeoutMs = 200).updateMe(TOKEN, rating = 1500f)
        assertTrue("expected Timeout, got $result", result is ApiResult.Timeout)
        // assertNull keeps the import warning quiet — same hygiene as the GET test.
        assertNull("Timeout has no payload", (result as? ApiResult.Success)?.data)
    }
}
