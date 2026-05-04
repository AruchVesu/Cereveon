package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpEngineEvalClient] against a real local HTTP server.
 *
 * Uses OkHttp MockWebServer to intercept connections and serve canned responses.
 * The production client uses [java.net.HttpURLConnection]; MockWebServer is
 * transport-agnostic.
 *
 * Contract reference: docs/API_CONTRACTS.md §1 — POST /engine/eval.
 * Key contract fact: the endpoint requires NO authentication (no X-Api-Key).
 *
 * Invariants pinned
 * -----------------
 *  1.  INT_EVAL_METHOD            request method is POST.
 *  2.  INT_EVAL_PATH              request path is /engine/eval.
 *  3.  INT_EVAL_CONTENT_TYPE      Content-Type header is application/json.
 *  4.  INT_EVAL_NO_AUTH_HEADER    X-Api-Key header is NOT sent (contract: no auth).
 *  5.  INT_EVAL_FEN_IN_BODY       fen field present in serialised request JSON.
 *  6.  INT_EVAL_SCORE_PARSED      integer score deserialised correctly.
 *  7.  INT_EVAL_BEST_MOVE_PARSED  best_move string deserialised correctly.
 *  8.  INT_EVAL_SOURCE_PARSED     source field deserialised correctly.
 *  9.  INT_EVAL_NULL_SCORE        JSON null score → EngineEvalResponse.score = null.
 * 10.  INT_EVAL_NULL_BEST_MOVE    empty/missing best_move → bestMove = null.
 * 11.  INT_EVAL_HTTP_NON_200      non-200 response → ApiResult.HttpError with correct code.
 */
class EngineEvalClientIntegrationTest {

    private lateinit var server: MockWebServer

    private val startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

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
    private fun client() = HttpEngineEvalClient(baseUrl = baseUrl())

    companion object {
        private const val EVAL_OK_BODY = """
{
  "score": 42,
  "best_move": "e2e4",
  "source": "engine",
  "_metrics": {"cache_hit": false, "engine_time_ms": 50}
}"""

        private const val EVAL_CACHE_BODY = """
{
  "score": -15,
  "best_move": "d7d5",
  "source": "cache",
  "_metrics": {"cache_hit": true}
}"""
    }

    // ---------------------------------------------------------------------------
    // 1–3  HTTP method, path, and Content-Type
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_EVAL_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_OK_BODY))
        client().evaluate(startingFen)
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_EVAL_PATH - request path is slash engine slash eval`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_OK_BODY))
        client().evaluate(startingFen)
        assertEquals("/engine/eval", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `INT_EVAL_CONTENT_TYPE - Content-Type is application slash json`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_OK_BODY))
        client().evaluate(startingFen)
        val ct = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Content-Type") ?: ""
        assertTrue("Content-Type must contain application/json, was: $ct",
            "application/json" in ct)
    }

    // ---------------------------------------------------------------------------
    // 4  No auth header — contract: POST /engine/eval requires no authentication
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_EVAL_NO_AUTH_HEADER - X-Api-Key is NOT sent (no auth per contract)`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_OK_BODY))
        client().evaluate(startingFen)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertNull(
            "X-Api-Key must not be sent to /engine/eval (no auth per docs/API_CONTRACTS.md §1)",
            req.getHeader("X-Api-Key"),
        )
    }

    // ---------------------------------------------------------------------------
    // 5  Request body
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_EVAL_FEN_IN_BODY - fen field present in serialised request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_OK_BODY))
        client().evaluate(startingFen)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(startingFen, body.getString("fen"))
    }

    // ---------------------------------------------------------------------------
    // 6–8  Response deserialisation — happy path
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_EVAL_SCORE_PARSED - integer score deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_OK_BODY))
        val result = client().evaluate(startingFen)
        assertTrue("Expected Success, got: $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as EngineEvalResponse
        assertEquals(42, data.score)
    }

    @Test
    fun `INT_EVAL_BEST_MOVE_PARSED - best_move string deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_OK_BODY))
        val result = client().evaluate(startingFen)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as EngineEvalResponse
        assertEquals("e2e4", data.bestMove)
    }

    @Test
    fun `INT_EVAL_SOURCE_PARSED - source field deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(EVAL_CACHE_BODY))
        val result = client().evaluate(startingFen)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as EngineEvalResponse
        assertEquals("cache", data.source)
    }

    // ---------------------------------------------------------------------------
    // 9–10  Nullable field handling
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_EVAL_NULL_SCORE - JSON null score maps to EngineEvalResponse score = null`() = runBlocking {
        val body = """{"score": null, "best_move": "e2e4", "source": "engine", "_metrics": {}}"""
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))
        val result = client().evaluate(startingFen)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as EngineEvalResponse
        assertNull("score must be null when JSON value is null", data.score)
    }

    @Test
    fun `INT_EVAL_NULL_BEST_MOVE - empty best_move maps to bestMove = null`() = runBlocking {
        val body = """{"score": 10, "best_move": "", "source": "engine", "_metrics": {}}"""
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))
        val result = client().evaluate(startingFen)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as EngineEvalResponse
        assertNull("bestMove must be null when best_move is empty string", data.bestMove)
    }

    // ---------------------------------------------------------------------------
    // 11  HTTP error codes
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_EVAL_HTTP_NON_200 - non-200 response returns HttpError with correct code`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(503)
            .setBody("""{"detail":"Service Unavailable"}"""))
        val result = client().evaluate(startingFen)
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(503, (result as ApiResult.HttpError).code)
    }
}
