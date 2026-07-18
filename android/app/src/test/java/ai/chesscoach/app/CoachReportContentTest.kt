package ai.chesscoach.app

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Integration tests for POST /coach/report — the client side of the
 * in-app "Report AI content" affordance (Google Play AI-Generated
 * Content policy, docs/API_CONTRACTS.md §45) — using [MockWebServer].
 *
 * Invariants pinned
 * -----------------
 *  1. RC_PATH_METHOD      request is POST /coach/report.
 *  2. RC_BEARER           Authorization: Bearer <token> is sent.
 *  3. RC_BODY_FIELDS      body carries content + surface + fen + reason.
 *  4. RC_OMITS_NULLS      null fen/reason are dropped from the wire.
 *  5. RC_SUCCESS          200 → ApiResult.Success.
 *  6. RC_HTTP_ERROR       500 → ApiResult.HttpError(500).
 *  7. RC_NETWORK_ERROR    unreachable server → NetworkError/Timeout, no throw.
 */
class CoachReportContentTest {

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

    private fun client() = HttpCoachApiClient(baseUrl = baseUrl(), apiKey = "test-key")

    private fun enqueueOk() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"received","id":"r1"}"""))
    }

    @Test
    fun `report uses POST coach report with bearer and full body`() = runBlocking {
        enqueueOk()

        client().reportContent(
            content = "an offensive coach reply",
            surface = "chat",
            fen = "8/8/8/8/8/8/8/K6k w - - 0 1",
            reason = "this is inappropriate",
            token = "jwt-tok",
        )

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("/coach/report", req.path)  // RC_PATH_METHOD
        assertEquals("POST", req.method)
        assertEquals("Bearer jwt-tok", req.getHeader("Authorization"))  // RC_BEARER
        val body = req.body.readUtf8()  // RC_BODY_FIELDS
        assertTrue(body.contains("an offensive coach reply"))
        assertTrue(body.contains("chat"))
        assertTrue(body.contains("this is inappropriate"))
        assertTrue(body.contains("8/8/8/8/8/8/8/K6k"))
    }

    @Test
    fun `report omits null fen and reason from the body`() = runBlocking {
        enqueueOk()

        client().reportContent(
            content = "bad reply",
            surface = "chat",
            fen = null,
            reason = null,
            token = "tok",
        )

        val body = server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8()
        assertTrue(body.contains("content"))
        assertTrue(body.contains("surface"))
        assertFalse("null fen must be dropped from the wire", body.contains("fen"))
        assertFalse("null reason must be dropped from the wire", body.contains("reason"))
    }

    @Test
    fun `HTTP 200 maps to Success`() = runBlocking {
        enqueueOk()
        val result = client().reportContent("x", "chat", null, null, "tok")
        assertTrue("Expected Success, got $result", result is ApiResult.Success)
    }

    @Test
    fun `HTTP 500 surfaces as HttpError 500`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500))
        val result = client().reportContent("x", "chat", null, null, "tok")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(500, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `unreachable server surfaces as NetworkError not a throw`() = runBlocking {
        val client = HttpCoachApiClient(
            baseUrl = "http://127.0.0.1:1",
            apiKey = "k",
            connectTimeoutMs = 500,
            readTimeoutMs = 500,
        )
        val result = client.reportContent("x", "chat", null, null, "tok")
        assertTrue(
            "Expected NetworkError or Timeout, got $result",
            result is ApiResult.NetworkError || result is ApiResult.Timeout,
        )
    }
}
