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
 * Integration tests for [HttpGameApiClient] against a real local HTTP server.
 *
 * Uses OkHttp MockWebServer to intercept connections and serve canned responses.
 * The production client uses [java.net.HttpURLConnection]; MockWebServer is
 * transport-agnostic.
 *
 * Contract references:
 *   docs/API_CONTRACTS.md §2  — GET  /next-training/{player_id}
 *   docs/API_CONTRACTS.md §3  — POST /game/finish
 *   (POST /game/start is undocumented but follows the same auth pattern)
 *
 * Invariants pinned
 * -----------------
 *  POST /game/start
 *  1.  INT_START_METHOD              request method is POST.
 *  2.  INT_START_PATH                request path is /game/start.
 *  3.  INT_START_CONTENT_TYPE        Content-Type is application/json.
 *  4.  INT_START_API_KEY             X-Api-Key header sent with configured value.
 *  5.  INT_START_PLAYER_ID_IN_BODY   player_id serialised in request body.
 *  6.  INT_START_GAME_ID_PARSED      game_id deserialised from response.
 *  7.  INT_START_HTTP_ERROR          non-200 → ApiResult.HttpError with correct code.
 *
 *  POST /game/finish (docs/API_CONTRACTS.md §3)
 *  8.  INT_FINISH_METHOD             request method is POST.
 *  9.  INT_FINISH_PATH               request path is /game/finish.
 * 10.  INT_FINISH_API_KEY            X-Api-Key header sent for /game/finish.
 * 11.  INT_FINISH_BODY_PGN           pgn field serialised in request body.
 * 12.  INT_FINISH_BODY_RESULT        result field serialised in request body.
 * 13.  INT_FINISH_BODY_ACCURACY      accuracy field serialised in request body.
 * 14.  INT_FINISH_BODY_WEAKNESSES    weaknesses object serialised in request body.
 * 15.  INT_FINISH_STATUS_PARSED      status="stored" deserialised correctly.
 * 16.  INT_FINISH_RATING_PARSED      new_rating deserialised as float.
 * 17.  INT_FINISH_CONFIDENCE_PARSED  confidence deserialised as float.
 * 18.  INT_FINISH_COACH_ACTION_TYPE  coach_action.type deserialised.
 * 19.  INT_FINISH_COACH_ACTION_WEAKNESS coach_action.weakness deserialised (nullable).
 * 20.  INT_FINISH_COACH_CONTENT_TITLE  coach_content.title deserialised.
 * 21.  INT_FINISH_COACH_CONTENT_DESC   coach_content.description deserialised.
 * 22.  INT_FINISH_BEARER_SENT        Authorization Bearer sent when tokenProvider returns value.
 *
 *  GET /next-training/{player_id} (docs/API_CONTRACTS.md §2)
 * 23.  INT_TRAINING_METHOD           request method is GET.
 * 24.  INT_TRAINING_PATH             request path contains player_id.
 * 25.  INT_TRAINING_API_KEY          X-Api-Key header sent.
 * 26.  INT_TRAINING_ALL_FIELDS       all four fields deserialised correctly.
 */
class GameApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    private val apiKey = "game-integration-key"

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

    private fun client(token: String? = null) = HttpGameApiClient(
        baseUrl = baseUrl(),
        apiKey = apiKey,
        tokenProvider = token?.let { { it } },
    )

    // ---------------------------------------------------------------------------
    // Shared response fixtures — match docs/API_CONTRACTS.md §3 schema exactly
    // ---------------------------------------------------------------------------

    companion object {
        private const val GAME_START_OK = """{"game_id": "game-abc-123"}"""

        private const val GAME_FINISH_OK = """
{
  "status": "stored",
  "new_rating": 1523.5,
  "confidence": 0.72,
  "learning": {"status": "updated"},
  "coach_action": {
    "type": "DRILL",
    "weakness": "endgame_technique",
    "reason": "Large negative learning delta triggered drill."
  },
  "coach_content": {
    "title": "Endgame Drill",
    "description": "Practice king and pawn endgames.",
    "payload": {"drill_id": "endgame-01"}
  }
}"""

        private const val NEXT_TRAINING_OK = """
{
  "topic": "tactics",
  "difficulty": 0.6,
  "format": "puzzle",
  "expected_gain": 8.5
}"""
    }

    // ---------------------------------------------------------------------------
    // 1–7  POST /game/start
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_START_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_START_OK))
        client().startGame("player1")
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_START_PATH - request path is slash game slash start`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_START_OK))
        client().startGame("player1")
        assertEquals("/game/start", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `INT_START_CONTENT_TYPE - Content-Type is application slash json`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_START_OK))
        client().startGame("player1")
        val ct = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Content-Type") ?: ""
        assertTrue("Content-Type must contain application/json, was: $ct",
            "application/json" in ct)
    }

    @Test
    fun `INT_START_API_KEY - X-Api-Key header matches configured key`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_START_OK))
        client().startGame("player1")
        assertEquals(apiKey, server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("X-Api-Key"))
    }

    @Test
    fun `INT_START_PLAYER_ID_IN_BODY - player_id serialised in request body`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_START_OK))
        client().startGame("player-test-99")
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("player-test-99", body.getString("player_id"))
    }

    @Test
    fun `INT_START_GAME_ID_PARSED - game_id deserialised from response`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_START_OK))
        val result = client().startGame("player1")
        assertTrue("Expected Success, got: $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameStartResponse
        assertEquals("game-abc-123", data.gameId)
    }

    @Test
    fun `INT_START_HTTP_ERROR - non-200 returns HttpError with correct code`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(503)
            .setBody("""{"detail":"Service Unavailable"}"""))
        val result = client().startGame("player1")
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(503, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_START_BEARER_SENT - Authorization Bearer sent for game start when tokenProvider returns value`() = runBlocking {
        // T3: /game/start now requires JWT; client must send Bearer when logged in.
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_START_OK))
        client(token = "bearer-token-xyz").startGame("player1")
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer bearer-token-xyz", req.getHeader("Authorization"))
    }

    // ---------------------------------------------------------------------------
    // 8–22  POST /game/finish
    // ---------------------------------------------------------------------------

    private fun finishReq(result: String = "win") = GameFinishRequest(
        pgn = "1.e4 e5 2.Nf3",
        result = result,
        accuracy = 0.85f,
        weaknesses = mapOf("endgame" to 0.6f),
        playerId = "player1",
    )

    @Test
    fun `INT_FINISH_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq())
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_FINISH_PATH - request path is slash game slash finish`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq())
        assertEquals("/game/finish", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `INT_FINISH_API_KEY - X-Api-Key header sent for game finish`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq())
        assertEquals(apiKey, server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("X-Api-Key"))
    }

    @Test
    fun `INT_FINISH_BODY_PGN - pgn serialised in request body`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq())
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("1.e4 e5 2.Nf3", body.getString("pgn"))
    }

    @Test
    fun `INT_FINISH_BODY_RESULT - result serialised in request body`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq(result = "loss"))
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("loss", body.getString("result"))
    }

    @Test
    fun `INT_FINISH_BODY_ACCURACY - accuracy serialised in request body`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq())
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals(0.85, body.getDouble("accuracy"), 0.001)
    }

    @Test
    fun `INT_FINISH_BODY_WEAKNESSES - weaknesses object serialised in request body`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq())
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        val weaknesses = body.getJSONObject("weaknesses")
        assertEquals(0.6, weaknesses.getDouble("endgame"), 0.001)
    }

    @Test
    fun `INT_FINISH_BODY_GAME_ID - game_id forwarded when non-null`() = runBlocking {
        // Resume restore reuses the original /game/start id so the
        // backend can mark the same `games` row complete instead of
        // orphaning it.
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq().copy(gameId = "game-abc-123"))
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("game-abc-123", body.getString("game_id"))
    }

    @Test
    fun `INT_FINISH_BODY_GAME_ID_OMITTED - game_id absent when null`() = runBlocking {
        // Backwards-compat: a finish call from a session that never
        // captured a server-side id (e.g. /game/start failed) must
        // still send a well-formed body that the backend treats as
        // "no resume link" rather than rejecting on a missing field.
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq())  // gameId defaults to null
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertFalse(
            "body must NOT contain game_id when caller passed null",
            body.has("game_id"),
        )
    }

    @Test
    fun `INT_FINISH_BODY_GAME_ID_BLANK_OMITTED - blank game_id treated as null`() = runBlocking {
        // Defensive: a stale-but-blank prefs entry must produce the
        // same wire shape as a true null.  Without this, a "" id would
        // serialise as game_id:"" and fail the backend's blank check.
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client().finishGame(finishReq().copy(gameId = "   "))
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertFalse(
            "body must NOT contain game_id when caller passed blank",
            body.has("game_id"),
        )
    }

    @Test
    fun `INT_FINISH_STATUS_PARSED - status stored deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        val result = client().finishGame(finishReq())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameFinishResponse
        assertEquals("stored", data.status)
    }

    @Test
    fun `INT_FINISH_RATING_PARSED - new_rating deserialised as float`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        val result = client().finishGame(finishReq())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameFinishResponse
        assertEquals(1523.5f, data.newRating, 0.01f)
    }

    @Test
    fun `INT_FINISH_CONFIDENCE_PARSED - confidence deserialised as float`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        val result = client().finishGame(finishReq())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameFinishResponse
        assertEquals(0.72f, data.confidence, 0.001f)
    }

    @Test
    fun `INT_FINISH_COACH_ACTION_TYPE - coach_action type deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        val result = client().finishGame(finishReq())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameFinishResponse
        assertEquals("DRILL", data.coachAction.type)
    }

    @Test
    fun `INT_FINISH_COACH_ACTION_WEAKNESS - coach_action weakness deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        val result = client().finishGame(finishReq())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameFinishResponse
        assertEquals("endgame_technique", data.coachAction.weakness)
    }

    @Test
    fun `INT_FINISH_COACH_CONTENT_TITLE - coach_content title deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        val result = client().finishGame(finishReq())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameFinishResponse
        assertEquals("Endgame Drill", data.coachContent.title)
    }

    @Test
    fun `INT_FINISH_COACH_CONTENT_DESC - coach_content description deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        val result = client().finishGame(finishReq())
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GameFinishResponse
        assertEquals("Practice king and pawn endgames.", data.coachContent.description)
    }

    @Test
    fun `INT_FINISH_BEARER_SENT - Authorization Bearer sent when tokenProvider returns value`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(GAME_FINISH_OK))
        client(token = "bearer-token-xyz").finishGame(finishReq())
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer bearer-token-xyz", req.getHeader("Authorization"))
    }

    // ---------------------------------------------------------------------------
    // 23–26  GET /next-training/{player_id}
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_TRAINING_METHOD - request uses HTTP GET`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(NEXT_TRAINING_OK))
        client().getNextTraining("player1")
        assertEquals("GET", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_TRAINING_PATH - request path contains player_id`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(NEXT_TRAINING_OK))
        client().getNextTraining("alice")
        val path = server.takeRequest(10, TimeUnit.SECONDS)!!.path ?: ""
        assertTrue("Path must contain player_id 'alice', was: $path",
            "/next-training/alice" in path)
    }

    @Test
    fun `INT_TRAINING_API_KEY - X-Api-Key header sent for next-training`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(NEXT_TRAINING_OK))
        client().getNextTraining("player1")
        assertEquals(apiKey, server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("X-Api-Key"))
    }

    @Test
    fun `INT_TRAINING_ALL_FIELDS - all four response fields deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(NEXT_TRAINING_OK))
        val result = client().getNextTraining("player1")
        assertTrue("Expected Success, got: $result", result is ApiResult.Success<*>)
        val rec = (result as ApiResult.Success<*>).data as TrainingRecommendation
        assertEquals("tactics", rec.topic)
        assertEquals(0.6f, rec.difficulty, 0.001f)
        assertEquals("puzzle", rec.format)
        assertEquals(8.5f, rec.expectedGain, 0.01f)
    }

    // ---------------------------------------------------------------------------
    // Cross-device resume — POST /game/{id}/checkpoint + GET /game/active
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_CHECKPOINT_BODY - fen + uci_history serialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"checkpointed"}"""))
        val result = client(token = "tok").checkpointGame(
            gameId = "game-abc",
            fen = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
            uciHistory = "e2e4,e7e5,g1f3",
        )
        assertTrue(result is ApiResult.Success<*>)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", req.method)
        assertEquals("/game/game-abc/checkpoint", req.path)
        val body = JSONObject(req.body.readUtf8())
        assertEquals(
            "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
            body.getString("fen"),
        )
        assertEquals("e2e4,e7e5,g1f3", body.getString("uci_history"))
    }

    @Test
    fun `INT_CHECKPOINT_BEARER - Authorization Bearer sent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"checkpointed"}"""))
        client(token = "bearer-token-xyz").checkpointGame("g1", "fen", "")
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer bearer-token-xyz", req.getHeader("Authorization"))
    }

    @Test
    fun `INT_CHECKPOINT_404_HTTP_ERROR - unknown game maps to HttpError(404)`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"game not found"}"""))
        val result = client(token = "tok").checkpointGame("missing", "fen", "")
        assertTrue("expected HttpError(404), got $result", result is ApiResult.HttpError)
        assertEquals(404, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_ACTIVE_GAME_PARSED - 200 returns Success with parsed body`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "game_id": "game-xyz",
                  "current_fen": "fen-from-server",
                  "current_uci_history": "e2e4,e7e5",
                  "last_checkpoint_at": "2026-04-27 12:34:56",
                  "started_at": "2026-04-27 12:30:00"
                }
                """.trimIndent(),
            ),
        )
        val result = client(token = "tok").getActiveGame()
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as ActiveGameResponse?
        assertNotNull(data)
        assertEquals("game-xyz", data!!.gameId)
        assertEquals("fen-from-server", data.currentFen)
        assertEquals("e2e4,e7e5", data.currentUciHistory)
    }

    @Test
    fun `INT_ACTIVE_GAME_404_IS_NULL_SUCCESS - no resumable game maps to Success(null)`() = runBlocking {
        // 404 is the documented "no resumable game" signal — must NOT
        // surface as ApiResult.HttpError(404), because the caller
        // (HomeActivity) treats absence-of-data and "fetch failed"
        // as different code paths.
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"no active game"}"""))
        val result = client(token = "tok").getActiveGame()
        assertTrue("expected Success(null), got $result", result is ApiResult.Success<*>)
        assertNull((result as ApiResult.Success<*>).data)
    }

    @Test
    fun `INT_ACTIVE_GAME_OTHER_HTTP_ERROR - non-404 maps to HttpError`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}"""))
        val result = client(token = "tok").getActiveGame()
        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(500, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `INT_DRILL_RESULT_BODY - outcome serialised + path correct`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"openings":[{"eco":"C84","name":"Ruy","line":"1.e4","mastery":0.85,"is_active":true,"ordinal":0}]}""",
            ),
        )
        client(token = "tok").recordDrillResult("C84", 1.0f)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", req.method)
        assertEquals("/repertoire/C84/drill-result", req.path)
        val body = JSONObject(req.body.readUtf8())
        assertEquals(1.0, body.getDouble("outcome"), 1e-3)
    }

    @Test
    fun `INT_DRILL_RESULT_404 - unknown eco maps to HttpError`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"opening not found"}"""))
        val result = client(token = "tok").recordDrillResult("ZZ9", 0.5f)
        assertTrue(result is ApiResult.HttpError)
        assertEquals(404, (result as ApiResult.HttpError).code)
    }

    // ---------------------------------------------------------------------------
    // GET /repertoire — opening list backing AtriumOpenings
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_REPERTOIRE_PARSED - 200 returns Success with parsed openings`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """
                {
                  "openings": [
                    {"eco":"C84","name":"Ruy Lopez","line":"1.e4 e5","mastery":0.78,"is_active":true,"ordinal":0},
                    {"eco":"B22","name":"Sicilian Alapin","line":"1.e4 c5","mastery":0.55,"is_active":false,"ordinal":1}
                  ]
                }
                """.trimIndent(),
            ),
        )
        val result = client(token = "tok").getRepertoire()
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        @Suppress("UNCHECKED_CAST")
        val openings = (result as ApiResult.Success<*>).data as List<RepertoireOpeningDto>
        assertEquals(2, openings.size)
        assertEquals("C84", openings[0].eco)
        assertEquals(0.78f, openings[0].mastery, 0.001f)
        assertTrue(openings[0].isActive)
        assertFalse(openings[1].isActive)
        assertEquals(1, openings[1].ordinal)
    }

    @Test
    fun `INT_REPERTOIRE_BEARER - Authorization Bearer sent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"openings":[]}"""))
        client(token = "bearer-rep-tok").getRepertoire()
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer bearer-rep-tok", req.getHeader("Authorization"))
        assertEquals("/repertoire", req.path)
    }

    @Test
    fun `INT_REPERTOIRE_EMPTY_OPENINGS - empty list parses cleanly`() = runBlocking {
        // Defensive: server contract says it never returns empty (it
        // substitutes defaults), but client must not crash if it ever
        // does — emptyList() is the right floor, NOT NullPointerException.
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"openings":[]}"""))
        val result = client(token = "tok").getRepertoire()
        assertTrue(result is ApiResult.Success<*>)
        @Suppress("UNCHECKED_CAST")
        assertTrue(((result as ApiResult.Success<*>).data as List<*>).isEmpty())
    }

    @Test
    fun `INT_REPERTOIRE_HTTP_ERROR - non-200 maps to HttpError`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"oops"}"""))
        val result = client(token = "tok").getRepertoire()
        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(500, (result as ApiResult.HttpError).code)
    }
}
