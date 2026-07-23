package com.cereveon.myapp

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpFeedbackApiClient] against MockWebServer.
 *
 * Contract reference: POST /feedback (docs/API_CONTRACTS.md §38).
 * Auth: X-Api-Key + JWT Bearer (route is `Depends(get_current_player)`
 * server-side).
 *
 * Invariants pinned
 * -----------------
 *  1. INT_FB_METHOD           request method is POST.
 *  2. INT_FB_PATH             request path is /feedback.
 *  3. INT_FB_CONTENT_TYPE     Content-Type is application/json.
 *  4. INT_FB_API_KEY_SENT     X-Api-Key header present.
 *  5. INT_FB_BEARER_SENT      Authorization: Bearer <jwt> present when the
 *                             tokenProvider returns a token.
 *  6. INT_FB_BEARER_ABSENT    Authorization absent when tokenProvider
 *                             returns null.
 *  7. INT_FB_WIRE_SHAPE       body is EXACTLY {"message", "app_version"}
 *                             — snake_case, no extra keys (the server's
 *                             Pydantic model is the other side of this pin).
 *  8. INT_FB_NULL_VERSION_OMITTED  appVersion=null → "app_version" key
 *                             absent (encodeDefaults=false in ApiJson).
 *  9. INT_FB_200_PARSED       200 body → ApiResult.Success with status + id.
 * 10. INT_FB_422_HTTP_ERR     422 (validation reject) → HttpError(422).
 * 11. INT_FB_429_HTTP_ERR     429 (rate limited) → HttpError(429).
 * 12. INT_FB_TOKEN_SINK       X-Auth-Token response header lands in tokenSink
 *                             (JWT rotation participation, §10).
 */
class FeedbackApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    private val apiKey = "test-api-key-feedback"

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

    private fun client(
        token: String? = "jwt-test-token",
        tokenSink: ((String) -> Unit)? = null,
    ) = HttpFeedbackApiClient(
        baseUrl = baseUrl(),
        apiKey = apiKey,
        tokenProvider = { token },
        tokenSink = tokenSink,
    )

    companion object {
        private const val SUBMIT_OK_BODY = """
{
  "status": "received",
  "id": "c9fdd598-5c34-47d2-bf61-a78de63f662a"
}"""
    }

    @Test
    fun `request wire shape is exactly the documented contract`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(SUBMIT_OK_BODY))

        client().submitFeedback("the board froze after castling", "1.4.2")

        val recorded = server.takeRequest(10, TimeUnit.SECONDS)
        assertNotNull(recorded)
        recorded!!
        // 1 + 2 + 3 + 4 + 5
        assertEquals("POST", recorded.method)
        assertEquals("/feedback", recorded.path)
        assertTrue(
            "Content-Type must be application/json",
            recorded.getHeader("Content-Type")!!.startsWith("application/json"),
        )
        assertEquals(apiKey, recorded.getHeader("X-Api-Key"))
        assertEquals("Bearer jwt-test-token", recorded.getHeader("Authorization"))

        // 7 — exact snake_case shape, no extra keys.
        val body = JSONObject(recorded.body.readUtf8())
        assertEquals("the board froze after castling", body.getString("message"))
        assertEquals("1.4.2", body.getString("app_version"))
        assertEquals(
            "submit body must carry exactly message + app_version",
            2, body.length(),
        )
    }

    @Test
    fun `null app version is omitted from the wire`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(SUBMIT_OK_BODY))

        client().submitFeedback("just words", null)

        val recorded = server.takeRequest(10, TimeUnit.SECONDS)
        assertNotNull(recorded)
        val body = JSONObject(recorded!!.body.readUtf8())
        assertEquals("just words", body.getString("message"))
        assertTrue(
            "app_version must be OMITTED (not null-valued) when unknown — " +
                "encodeDefaults=false is the wire contract",
            !body.has("app_version"),
        )
        assertEquals(1, body.length())
    }

    @Test
    fun `bearer header is absent when token provider returns null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(SUBMIT_OK_BODY))

        client(token = null).submitFeedback("msg", "1.0")

        val recorded = server.takeRequest(10, TimeUnit.SECONDS)
        assertNotNull(recorded)
        assertNull(
            "no Authorization header may be fabricated for a logged-out caller",
            recorded!!.getHeader("Authorization"),
        )
    }

    @Test
    fun `http 200 parses into Success with status and id`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(SUBMIT_OK_BODY))

        val result = client().submitFeedback("msg", "1.0")

        assertTrue("expected Success, got $result", result is ApiResult.Success)
        val data = (result as ApiResult.Success).data
        assertEquals("received", data.status)
        assertEquals("c9fdd598-5c34-47d2-bf61-a78de63f662a", data.id)
    }

    @Test
    fun `http 422 validation reject maps to HttpError 422`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(422)
                .setBody("""{"detail": [{"loc": ["body", "message"], "msg": "message must not be empty", "type": "value_error"}]}"""),
        )

        val result = client().submitFeedback("   ", "1.0")

        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(422, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `http 429 rate limited maps to HttpError 429`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(429)
                .setBody("""{"error": "Too many requests"}"""),
        )

        val result = client().submitFeedback("msg", "1.0")

        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(429, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `x-auth-token response header reaches the token sink`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody(SUBMIT_OK_BODY)
                .setHeader("X-Auth-Token", "rotated-jwt-42"),
        )
        var sunk: String? = null

        client(tokenSink = { sunk = it }).submitFeedback("msg", "1.0")

        assertEquals("rotated-jwt-42", sunk)
    }
}
