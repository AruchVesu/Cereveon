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
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpCoachApiClient] against a real local HTTP server.
 *
 * Uses OkHttp MockWebServer to intercept connections and serve canned responses.
 * The production client uses [java.net.HttpURLConnection]; MockWebServer is
 * transport-agnostic and works with any HTTP client.
 *
 * Contract reference: docs/API_CONTRACTS.md (POST /chat — no numbered section).
 *
 * Invariants pinned
 * -----------------
 *  1.  INT_CHAT_METHOD               request method is POST.
 *  2.  INT_CHAT_PATH                 request path is /chat.
 *  3.  INT_CHAT_CONTENT_TYPE         Content-Type header is application/json.
 *  4.  INT_CHAT_API_KEY              X-Api-Key header matches configured value.
 *  5.  INT_CHAT_FEN_IN_BODY          fen field present in serialised request JSON.
 *  6.  INT_CHAT_MESSAGES_IN_BODY     messages array serialised with role and content.
 *  7.  INT_CHAT_PROFILE_IN_BODY      player_profile serialised when non-null.
 *  8.  INT_CHAT_PROFILE_OMITTED      player_profile absent from body when null.
 *  9.  INT_CHAT_MISTAKES_IN_BODY     past_mistakes serialised when non-null.
 * 10.  INT_CHAT_MISTAKES_OMITTED     past_mistakes absent from body when null.
 * 11.  INT_CHAT_REPLY_PARSED         reply field deserialised into ChatResponseBody.
 * 12.  INT_CHAT_SIGNAL_BAND_PARSED   engine_signal.evaluation.band deserialised.
 * 13.  INT_CHAT_SIGNAL_PHASE_PARSED  engine_signal.phase deserialised.
 * 14.  INT_CHAT_SIGNAL_NULL          absent engine_signal → engineSignal = null.
 * 15.  INT_CHAT_HTTP_401             non-200 401 → ApiResult.HttpError(401).
 * 16.  INT_CHAT_HTTP_422             non-200 422 → ApiResult.HttpError(422).
 * 17.  INT_CHAT_HTTP_500             non-200 500 → ApiResult.HttpError(500).
 * 18.  INT_CHAT_BEARER_SENT         Authorization Bearer header sent when tokenProvider is set.
 * 19.  INT_CHAT_BEARER_ABSENT        Authorization header absent when tokenProvider is null.
 */
class CoachApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    private val startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    private val apiKey = "integration-test-key"

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

    private fun client(token: String? = null): HttpCoachApiClient = HttpCoachApiClient(
        baseUrl = baseUrl(),
        apiKey = apiKey,
        tokenProvider = token?.let { { it } },
    )

    // ---------------------------------------------------------------------------
    // Shared mock response bodies
    // ---------------------------------------------------------------------------

    companion object {
        private const val CHAT_OK_BODY = """
{
  "reply": "Engine shows equality in the opening.",
  "engine_signal": {
    "evaluation": { "band": "equal", "side": "white" },
    "eval_delta": "stable",
    "last_move_quality": "unknown",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "opening"
  },
  "mode": "CHAT_V1"
}"""
    }

    // ---------------------------------------------------------------------------
    // 1–2  HTTP method and path
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_CHAT_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList())
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_CHAT_PATH - request path is slash chat`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList())
        assertEquals("/chat", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    // ---------------------------------------------------------------------------
    // 3–4  Request headers
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_CHAT_CONTENT_TYPE - Content-Type is application slash json`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList())
        val ct = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Content-Type") ?: ""
        assertTrue("Content-Type must contain application/json, was: $ct",
            "application/json" in ct)
    }

    @Test
    fun `INT_CHAT_API_KEY - X-Api-Key matches configured key`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList())
        assertEquals(apiKey, server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("X-Api-Key"))
    }

    // ---------------------------------------------------------------------------
    // 5–10  Request body serialisation
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_CHAT_FEN_IN_BODY - fen field present in request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList())
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(startingFen, body.getString("fen"))
    }

    @Test
    fun `INT_CHAT_MESSAGES_IN_BODY - messages serialised with role and content`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        val msgs = listOf(ChatMessageDto("user", "What is the best plan?"))
        client().chat(startingFen, msgs)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        val arr = body.getJSONArray("messages")
        assertEquals(1, arr.length())
        assertEquals("user", arr.getJSONObject(0).getString("role"))
        assertEquals("What is the best plan?", arr.getJSONObject(0).getString("content"))
    }

    @Test
    fun `INT_CHAT_PROFILE_IN_BODY - player_profile serialised when non-null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        val profile = PlayerProfileDto(rating = 1500f, confidence = 0.8f)
        client().chat(startingFen, emptyList(), playerProfile = profile)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertTrue("player_profile key missing from body", body.has("player_profile"))
        val pp = body.getJSONObject("player_profile")
        assertEquals(1500.0, pp.getDouble("rating"), 0.01)
        assertEquals(0.8, pp.getDouble("confidence"), 0.01)
    }

    @Test
    fun `INT_CHAT_PROFILE_OMITTED - player_profile absent when null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList(), playerProfile = null)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertFalse("player_profile must be absent when null", body.has("player_profile"))
    }

    @Test
    fun `INT_CHAT_MISTAKES_IN_BODY - past_mistakes serialised when non-null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList(), pastMistakes = listOf("tactics", "endgame"))
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertTrue("past_mistakes key missing from body", body.has("past_mistakes"))
        val arr = body.getJSONArray("past_mistakes")
        assertEquals(2, arr.length())
        assertEquals("tactics", arr.getString(0))
        assertEquals("endgame", arr.getString(1))
    }

    @Test
    fun `INT_CHAT_MISTAKES_OMITTED - past_mistakes absent when null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client().chat(startingFen, emptyList(), pastMistakes = null)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertFalse("past_mistakes must be absent when null", body.has("past_mistakes"))
    }

    // ---------------------------------------------------------------------------
    // 11–14  Response deserialisation
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_CHAT_REPLY_PARSED - reply field deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        val result = client().chat(startingFen, emptyList())
        assertTrue("Expected Success, got: $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as ChatResponseBody
        assertEquals("Engine shows equality in the opening.", data.reply)
    }

    @Test
    fun `INT_CHAT_SIGNAL_BAND_PARSED - engine_signal evaluation band deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        val result = client().chat(startingFen, emptyList())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as ChatResponseBody
        assertNotNull("engineSignal must not be null", data.engineSignal)
        assertEquals("equal", data.engineSignal!!.evaluation?.band)
    }

    @Test
    fun `INT_CHAT_SIGNAL_PHASE_PARSED - engine_signal phase deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        val result = client().chat(startingFen, emptyList())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as ChatResponseBody
        assertEquals("opening", data.engineSignal?.phase)
    }

    @Test
    fun `INT_CHAT_SIGNAL_NULL - absent engine_signal parsed as null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200)
            .setBody("""{"reply":"No signal test."}"""))
        val result = client().chat(startingFen, emptyList())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as ChatResponseBody
        assertNull("engineSignal must be null when absent from response", data.engineSignal)
    }

    // ---------------------------------------------------------------------------
    // 15–17  HTTP error codes mapped to ApiResult.HttpError
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_CHAT_HTTP_401 - returns HttpError with code 401`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401)
            .setBody("""{"detail":"Unauthorized"}"""))
        val result = client().chat(startingFen, emptyList())
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_CHAT_HTTP_422 - returns HttpError with code 422`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(422)
            .setBody("""{"detail":"Unprocessable Entity"}"""))
        val result = client().chat(startingFen, emptyList())
        assertTrue(result is ApiResult.HttpError)
        assertEquals(422, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_CHAT_HTTP_500 - returns HttpError with code 500`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500)
            .setBody("""{"detail":"Internal Server Error"}"""))
        val result = client().chat(startingFen, emptyList())
        assertTrue(result is ApiResult.HttpError)
        assertEquals(500, (result as ApiResult.HttpError).code)
    }

    // ---------------------------------------------------------------------------
    // 18–19  Bearer token injection
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_CHAT_BEARER_SENT - Authorization Bearer sent when tokenProvider is set`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client(token = "jwt-abc-123").chat(startingFen, emptyList())
        assertEquals("Bearer jwt-abc-123",
            server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Authorization"))
    }

    @Test
    fun `INT_CHAT_BEARER_ABSENT - Authorization header absent when tokenProvider is null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CHAT_OK_BODY))
        client(token = null).chat(startingFen, emptyList())
        assertNull("Authorization header must be absent when tokenProvider is null",
            server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Authorization"))
    }

    // ---------------------------------------------------------------------------
    // 20–24  submitFeedback — POST /game/coach-feedback
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_FEEDBACK_PATH - submitFeedback posts to slash game slash coach-feedback`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"recorded"}"""))
        client().submitFeedback(startingFen, isHelpful = true, token = null)
        assertEquals("/game/coach-feedback", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `INT_FEEDBACK_METHOD - submitFeedback uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"recorded"}"""))
        client().submitFeedback(startingFen, isHelpful = false, token = null)
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_FEEDBACK_BODY - session_fen and is_helpful serialised in body`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"recorded"}"""))
        client().submitFeedback(startingFen, isHelpful = true, token = null)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(startingFen, body.getString("session_fen"))
        assertTrue("is_helpful must be true", body.getBoolean("is_helpful"))
    }

    @Test
    fun `INT_FEEDBACK_BEARER - Authorization Bearer sent when token is non-null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"recorded"}"""))
        client(token = "feedback-token").submitFeedback(startingFen, isHelpful = true, token = "feedback-token")
        assertEquals("Bearer feedback-token",
            server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Authorization"))
    }

    @Test
    fun `INT_FEEDBACK_HTTP_200 - 200 response returns ApiResult Success`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"recorded"}"""))
        val result = client().submitFeedback(startingFen, isHelpful = false, token = null)
        assertTrue("Expected Success, got: $result", result is ApiResult.Success<*>)
    }

    @Test
    fun `INT_FEEDBACK_HTTP_401 - 401 response returns ApiResult HttpError`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Unauthorized"}"""))
        val result = client().submitFeedback(startingFen, isHelpful = true, token = null)
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }
}
