package ai.chesscoach.app

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
 * Integration tests for [HttpLiveMoveClient] against a real local HTTP server.
 *
 * Uses OkHttp MockWebServer to intercept connections and serve canned responses.
 * The production client uses [java.net.HttpURLConnection]; MockWebServer is
 * transport-agnostic.
 *
 * Contract reference: POST /live/move (server.py).
 * Auth: X-Api-Key for rate-limit / shared dependency chain, plus a JWT
 * Bearer token because `live_move` is gated by
 * `Depends(get_current_player)` on the server.  Missing Bearer →
 * 401 "Missing token" and the inline Mode-1 hint silently never lands.
 *
 * Invariants pinned
 * -----------------
 *  1. INT_LIVE_METHOD           request method is POST.
 *  2. INT_LIVE_PATH             request path is /live/move.
 *  3. INT_LIVE_CONTENT_TYPE     Content-Type header is application/json.
 *  4. INT_LIVE_API_KEY_SENT     X-Api-Key header is present.
 *  4b.INT_LIVE_BEARER_SENT      Authorization: Bearer <jwt> is present
 *                               when tokenProvider returns a non-null token.
 *  4c.INT_LIVE_BEARER_ABSENT_WHEN_TOKEN_NULL
 *                               Authorization header is absent when
 *                               tokenProvider returns null
 *                               (logged-out / pre-auth window).
 *  5. INT_LIVE_FEN_IN_BODY      fen field present in request JSON.
 *  6. INT_LIVE_UCI_IN_BODY      uci field present in request JSON.
 *  7. INT_LIVE_PLAYER_ID_BODY   player_id field present in request JSON.
 *  7b.INT_LIVE_FEN_BEFORE_IN_BODY   fen_before present when supplied (move-quality input).
 *  7c.INT_LIVE_FEN_BEFORE_ABSENT    fen_before omitted (not null) when not supplied.
 *  8. INT_LIVE_HINT_PARSED      hint field deserialised correctly.
 *  9. INT_LIVE_MODE_PARSED      mode field deserialised correctly.
 * 10. INT_LIVE_QUALITY_PARSED   move_quality field deserialised correctly.
 * 11. INT_LIVE_HTTP_NON_200     non-200 response → ApiResult.HttpError with correct code.
 * 12. INT_LIVE_TIMEOUT          connection timeout → ApiResult.Timeout.
 * 13. INT_LIVE_EMPTY_HINT       empty hint string is preserved (not replaced with null).
 * 14. INT_LIVE_ENGINE_SIGNAL_PARSED   engine_signal object is deserialised into EngineSignalDto.
 * 15. INT_LIVE_ENGINE_SIGNAL_BAND     engine_signal.evaluation.band is parsed correctly.
 * 16. INT_LIVE_ENGINE_SIGNAL_PHASE    engine_signal.phase is parsed correctly.
 * 17. INT_LIVE_ENGINE_SIGNAL_ABSENT   missing engine_signal field → engineSignal is null.
 */
class LiveMoveApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    private val apiKey = "test-api-key-live"
    private val startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    private val testUci = "e2e4"

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

    private fun client(connectTimeoutMs: Int = 8_000, readTimeoutMs: Int = 15_000) =
        HttpLiveMoveClient(
            baseUrl = baseUrl(),
            apiKey = apiKey,
            // Tests that don't assert on auth pass a null-returning
            // provider — the Bearer-presence tests below override this
            // with their own client instance.  Required (not defaulted)
            // by HttpLiveMoveClient so every callsite makes an explicit
            // choice and the Mode-1 401 bug can't regress by omission.
            tokenProvider = { null },
            connectTimeoutMs = connectTimeoutMs,
            readTimeoutMs = readTimeoutMs,
        )

    companion object {
        private const val LIVE_OK_BODY = """
{
  "status": "ok",
  "hint": "Engine: white has equal [opening]. Keep developing your pieces and controlling the centre.",
  "engine_signal": {
    "evaluation": {"type": "cp", "band": "equal", "side": "white"},
    "eval_delta": "stable",
    "last_move_quality": "good",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "opening"
  },
  "move_quality": "good",
  "mode": "LIVE_V1"
}"""

        private const val LIVE_BLUNDER_BODY = """
{
  "status": "ok",
  "hint": "Engine: black has a decisive advantage [middlegame]. That was a blunder — try to find a better continuation.",
  "engine_signal": {
    "evaluation": {"type": "cp", "band": "decisive_advantage", "side": "black"},
    "eval_delta": "declining",
    "last_move_quality": "blunder",
    "tactical_flags": ["hanging_piece"],
    "position_flags": [],
    "phase": "middlegame"
  },
  "move_quality": "blunder",
  "mode": "LIVE_V1"
}"""
    }

    // ---------------------------------------------------------------------------
    // 1–3  HTTP method, path, and Content-Type
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_LIVE_PATH - request path is slash live slash move`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        assertEquals("/live/move", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `INT_LIVE_CONTENT_TYPE - Content-Type is application slash json`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        val ct = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Content-Type") ?: ""
        assertTrue("Content-Type must contain application/json, was: $ct",
            "application/json" in ct)
    }

    // ---------------------------------------------------------------------------
    // 4  Auth header
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_API_KEY_SENT - X-Api-Key header is sent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals(
            "X-Api-Key must equal the configured API key",
            apiKey,
            req.getHeader("X-Api-Key"),
        )
    }

    @Test
    fun `INT_LIVE_BEARER_SENT - Authorization Bearer header is sent when tokenProvider yields a JWT`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val jwt = "header.payload.signature"
        val client = HttpLiveMoveClient(
            baseUrl = baseUrl(),
            apiKey = apiKey,
            tokenProvider = { jwt },
        )
        client.getLiveCoaching(startingFen, testUci)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals(
            "Authorization must be 'Bearer <jwt>' so /live/move can resolve the player",
            "Bearer $jwt",
            req.getHeader("Authorization"),
        )
    }

    @Test
    fun `INT_LIVE_BEARER_ABSENT_WHEN_TOKEN_NULL - Authorization header is absent when tokenProvider returns null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val client = HttpLiveMoveClient(
            baseUrl = baseUrl(),
            apiKey = apiKey,
            tokenProvider = { null },
        )
        client.getLiveCoaching(startingFen, testUci)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertNull(
            "Authorization header must be absent when tokenProvider returns null (logged-out window)",
            req.getHeader("Authorization"),
        )
    }

    // ---------------------------------------------------------------------------
    // 5–7  Request body fields
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_FEN_IN_BODY - fen field present in serialised request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(startingFen, body.getString("fen"))
    }

    @Test
    fun `INT_LIVE_UCI_IN_BODY - uci field present in serialised request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(testUci, body.getString("uci"))
    }

    @Test
    fun `INT_LIVE_PLAYER_ID_BODY - player_id field present in serialised request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci, playerId = "test-player")
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("test-player", body.getString("player_id"))
    }

    @Test
    fun `INT_LIVE_FEN_BEFORE_IN_BODY - fen_before present when supplied`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val before = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        client().getLiveCoaching(startingFen, testUci, fenBefore = before)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(
            "fen_before must carry the pre-move FEN so the server can grade move quality",
            before,
            body.getString("fen_before"),
        )
    }

    @Test
    fun `INT_LIVE_FEN_BEFORE_ABSENT - fen_before omitted when not supplied`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertTrue(
            "fen_before must be omitted (not null) when not supplied — encodeDefaults=false",
            !body.has("fen_before"),
        )
    }

    // ---------------------------------------------------------------------------
    // 8–10  Response deserialisation
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_HINT_PARSED - hint field deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue("Expected Success, got: $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertTrue(
            "hint must be non-empty, was: '${data.hint}'",
            data.hint.isNotBlank(),
        )
        assertTrue(
            "hint must reference evaluation context",
            "equal" in data.hint.lowercase() || "advantage" in data.hint.lowercase(),
        )
    }

    @Test
    fun `INT_LIVE_MODE_PARSED - mode field deserialised as LIVE_V1`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertEquals("LIVE_V1", data.mode)
    }

    @Test
    fun `INT_LIVE_QUALITY_PARSED - move_quality field deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_BLUNDER_BODY))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertEquals("blunder", data.moveQuality)
    }

    // ---------------------------------------------------------------------------
    // 11  HTTP error codes
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_HTTP_NON_200 - non-200 response returns HttpError with correct code`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(429).setBody("""{"error":"Too many requests"}"""))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(429, (result as ApiResult.HttpError).code)
    }

    // ---------------------------------------------------------------------------
    // 12  Timeout handling
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_TIMEOUT - short read timeout returns ApiResult Timeout`() = runBlocking {
        // Enqueue a response that never arrives (connection closed immediately).
        // Using a 1 ms read timeout ensures SocketTimeoutException is raised.
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBodyDelay(500, TimeUnit.MILLISECONDS)
                .setBody(LIVE_OK_BODY),
        )
        val result = client(readTimeoutMs = 1).getLiveCoaching(startingFen, testUci)
        assertTrue(
            "Expected Timeout on slow server, got: $result",
            result is ApiResult.Timeout,
        )
    }

    // ---------------------------------------------------------------------------
    // 13  Empty hint preserved
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_EMPTY_HINT - empty hint string is preserved not replaced with null`() = runBlocking {
        val body = """{"status":"ok","hint":"","move_quality":"unknown","mode":"LIVE_V1"}"""
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertNotNull("hint must not be null even when empty", data.hint)
        assertEquals("", data.hint)
    }

    // ---------------------------------------------------------------------------
    // 14–17  engine_signal deserialisation
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_ENGINE_SIGNAL_PARSED - engine_signal is deserialised into EngineSignalDto`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertNotNull("engineSignal must be non-null when engine_signal is present", data.engineSignal)
    }

    @Test
    fun `INT_LIVE_ENGINE_SIGNAL_BAND - engine_signal evaluation band is parsed correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertEquals(
            "evaluation.band must be 'equal' for the LIVE_OK_BODY fixture",
            "equal",
            data.engineSignal?.evaluation?.band,
        )
    }

    @Test
    fun `INT_LIVE_ENGINE_SIGNAL_PHASE - engine_signal phase is parsed correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertEquals(
            "phase must be 'opening' for the LIVE_OK_BODY fixture",
            "opening",
            data.engineSignal?.phase,
        )
    }

    @Test
    fun `INT_LIVE_ENGINE_SIGNAL_ABSENT - missing engine_signal field results in null`() = runBlocking {
        val body = """{"status":"ok","hint":"","move_quality":"unknown","mode":"LIVE_V1"}"""
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertNull("engineSignal must be null when engine_signal absent from response", data.engineSignal)
    }

    // ---------------------------------------------------------------------------
    // 18–19  X-Auth-Token sliding-refresh consumption
    //
    // POST /live/move depends on `get_current_player` (server.py — see
    // `Depends(get_current_player)` on `live_move`), so the server attaches a
    // freshly-minted JWT to every 200 response in the `X-Auth-Token` header
    // (docs/API_CONTRACTS.md §10). The client was previously discarding the
    // header; these two invariants pin that the configured [tokenSink] is
    // invoked when the header is present and is NOT invoked when it's absent.
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_AUTH_TOKEN_CONSUMED - tokenSink receives X-Auth-Token from response`() = runBlocking {
        val refreshed = "refreshed.jwt.payload"
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("X-Auth-Token", refreshed)
                .setBody(LIVE_OK_BODY),
        )
        val sunk = mutableListOf<String>()
        val client = HttpLiveMoveClient(
            baseUrl = baseUrl(),
            apiKey = apiKey,
            tokenProvider = { null },
            tokenSink = { sunk += it },
        )
        client.getLiveCoaching(startingFen, testUci)
        assertEquals(
            "tokenSink must receive the refreshed JWT exactly once",
            listOf(refreshed),
            sunk,
        )
    }

    @Test
    fun `INT_LIVE_AUTH_TOKEN_ABSENT_NOOP - tokenSink not invoked when header missing`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val sunk = mutableListOf<String>()
        val client = HttpLiveMoveClient(
            baseUrl = baseUrl(),
            apiKey = apiKey,
            tokenProvider = { null },
            tokenSink = { sunk += it },
        )
        client.getLiveCoaching(startingFen, testUci)
        assertTrue(
            "tokenSink must not be invoked when response carries no X-Auth-Token; got: $sunk",
            sunk.isEmpty(),
        )
    }

    // ---------------------------------------------------------------------------
    // 20–23  Entitlements: game_id request field + coach_tier response field
    //        (API_CONTRACTS.md §4, additive 2026-07)
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_GAME_ID_IN_BODY - game_id present when supplied`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci, gameId = "srv-game-42")
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(
            "game_id must reach the wire so the server can meter coached GAMES",
            "srv-game-42",
            body.getString("game_id"),
        )
    }

    @Test
    fun `INT_LIVE_GAME_ID_ABSENT - game_id omitted when not supplied`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        client().getLiveCoaching(startingFen, testUci)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertTrue(
            "game_id must be OMITTED (not null) when unknown — the server fails " +
                "open for absent ids, and encodeDefaults=false guarantees omission",
            !body.has("game_id"),
        )
    }

    @Test
    fun `INT_LIVE_COACH_TIER_PARSED - coach_tier is deserialised when present`() = runBlocking {
        val body = """
{
  "status": "ok",
  "hint": "Solid choice.",
  "engine_signal": null,
  "move_quality": "good",
  "mode": "LIVE_V1",
  "coach_tier": {"plan": "free", "degraded": true, "remaining": 0}
}"""
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertEquals("free", data.coachTier?.plan)
        assertEquals(true, data.coachTier?.degraded)
        assertEquals(0, data.coachTier?.remaining)
    }

    @Test
    fun `INT_LIVE_COACH_TIER_ABSENT - pre-entitlements server yields null coachTier`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LIVE_OK_BODY))
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LiveMoveResponse
        assertNull(
            "coachTier must be null (not-metered) when the server omits coach_tier",
            data.coachTier,
        )
    }

    // ---------------------------------------------------------------------------
    // 24  Error bodies surface on ApiResult.HttpError (structured error
    //     contracts — e.g. the entitlements 402 on the chat routes)
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_LIVE_HTTP_ERROR_BODY - non-200 carries the error body`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(402)
                .setBody("""{"error": "chat_daily_limit", "plan": "free", "limit": 3, "used": 3}"""),
        )
        val result = client().getLiveCoaching(startingFen, testUci)
        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        val httpError = result as ApiResult.HttpError
        assertEquals(402, httpError.code)
        assertTrue(
            "HttpError.body must carry the structured error payload; got: ${httpError.body}",
            httpError.body?.contains("chat_daily_limit") == true,
        )
    }
}
