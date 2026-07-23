package com.cereveon.myapp

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
    // 23–26  RETIRED in PR 26 (2026-05-15) alongside the
    // /next-training/{player_id} endpoint + the ``getNextTraining`` API
    // method.  Wire-shape pins (HTTP GET, path includes player_id,
    // X-Api-Key header, four-field deserialisation) defended a
    // call site that no longer exists.

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

    // ---------------------------------------------------------------------------
    // POST /coach/plan/puzzle/complete (docs/API_CONTRACTS.md §35)
    // ---------------------------------------------------------------------------

    private val coachPlanCompletedJson = """
{
  "plan_id": "plan-xyz",
  "theme": "king_safety",
  "verdict": "",
  "anchor_category": "tactical_vision",
  "status": "completed",
  "total_days": 3,
  "today_puzzle": null,
  "days": [
    {"day_offset": 0, "due_at": "2026-06-20T00:00:00", "completed": true, "is_due": false, "source_type": "original"},
    {"day_offset": 3, "due_at": "2026-06-23T00:00:00", "completed": true, "is_due": false, "source_type": "library"},
    {"day_offset": 7, "due_at": "2026-06-27T00:00:00", "completed": true, "is_due": false, "source_type": "library"}
  ]
}"""

    @Test
    fun `INT_PLAN_COMPLETE_METHOD_PATH - POST to complete endpoint`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(coachPlanCompletedJson))
        client(token = "tok").completePlanPuzzle(planId = "plan-xyz", dayOffset = 7)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", req.method)
        assertEquals("/coach/plan/puzzle/complete", req.path)
    }

    @Test
    fun `INT_PLAN_COMPLETE_BODY - plan_id and day_offset serialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(coachPlanCompletedJson))
        client(token = "tok").completePlanPuzzle(planId = "plan-xyz", dayOffset = 3)
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("plan-xyz", body.getString("plan_id"))
        assertEquals(3, body.getInt("day_offset"))
    }

    @Test
    fun `INT_PLAN_COMPLETE_BEARER - Bearer sent, no X-Api-Key`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(coachPlanCompletedJson))
        client(token = "bearer-plan-tok").completePlanPuzzle("plan-xyz", 0)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer bearer-plan-tok", req.getHeader("Authorization"))
        assertNull(req.getHeader("X-Api-Key"))
    }

    @Test
    fun `INT_PLAN_COMPLETE_PARSED - status, anchor_category, and days deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(coachPlanCompletedJson))
        val result = client(token = "tok").completePlanPuzzle("plan-xyz", 7)
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as CoachPlanResponse
        assertEquals("completed", data.status)
        assertEquals("tactical_vision", data.anchorCategory)
        assertEquals(3, data.days.size)
        assertTrue(data.days.all { it.completed })
        assertNull(data.todayPuzzle)
    }

    @Test
    fun `INT_PLAN_COMPLETE_HTTP_ERROR - 404 maps to HttpError`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(404).setBody("""{"detail":"study plan not found"}""")
        )
        val result = client(token = "tok").completePlanPuzzle("nope", 0)
        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(404, (result as ApiResult.HttpError).code)
    }

    // ---------------------------------------------------------------------------
    // GET /coach/plan/today — decode coverage for the new overview fields
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_PLAN_TODAY_LEGACY_SHAPE - response without new fields decodes with defaults`() =
        runBlocking {
            // A server response predating the status / anchor_category /
            // days[] fields must still decode leniently: status defaults
            // to "active", anchor_category to null, days to empty.
            val legacy =
                """{"plan_id":"p1","theme":"king_safety","verdict":"","total_days":3,"today_puzzle":null}"""
            server.enqueue(MockResponse().setResponseCode(200).setBody(legacy))
            val result = client(token = "tok").getCoachPlanToday()
            assertTrue(result is ApiResult.Success<*>)
            val data = (result as ApiResult.Success<*>).data as CoachPlanResponse
            assertEquals("active", data.status)
            assertNull(data.anchorCategory)
            assertTrue(data.days.isEmpty())
        }

    @Test
    fun `INT_PLAN_TODAY_NULL_BODY - JSON null decodes to a null plan`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("null"))
        val result = client(token = "tok").getCoachPlanToday()
        assertTrue(result is ApiResult.Success<*>)
        assertNull((result as ApiResult.Success<*>).data)
    }

    @Test
    fun `INT_PLAN_TODAY_SOLUTION_LINE - today_puzzle decodes its walk line`() = runBlocking {
        val withLine = """
{
  "plan_id": "p2",
  "theme": "queen_safety",
  "verdict": "",
  "anchor_category": null,
  "status": "active",
  "total_days": 3,
  "today_puzzle": {
    "day_offset": 3,
    "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2",
    "expected_move_uci": "b8c6",
    "solution_line_uci": ["b8c6", "f1c4", "g8f6"],
    "source_type": "library",
    "due_at": "2026-07-15T00:00:00"
  },
  "days": []
}"""
        server.enqueue(MockResponse().setResponseCode(200).setBody(withLine))
        val result = client(token = "tok").getCoachPlanToday()
        assertTrue(result is ApiResult.Success<*>)
        val puzzle = ((result as ApiResult.Success<*>).data as CoachPlanResponse).todayPuzzle!!
        assertEquals(listOf("b8c6", "f1c4", "g8f6"), puzzle.solutionLineUci)
        assertEquals("b8c6", puzzle.expectedMoveUci)
    }

    @Test
    fun `INT_PLAN_TODAY_SOLUTION_LINE_ABSENT - legacy today_puzzle defaults to empty walk`() =
        runBlocking {
            // A server predating solution_line_uci (or a day-0 original,
            // which sends []) must decode to an empty list — the sheet then
            // runs the single-decision drill.
            val legacyPuzzle = """
{
  "plan_id": "p3",
  "theme": "king_safety",
  "verdict": "",
  "total_days": 3,
  "today_puzzle": {
    "day_offset": 0,
    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "expected_move_uci": "f3e5",
    "source_type": "original",
    "due_at": "2026-07-15T00:00:00"
  },
  "days": []
}"""
            server.enqueue(MockResponse().setResponseCode(200).setBody(legacyPuzzle))
            val result = client(token = "tok").getCoachPlanToday()
            assertTrue(result is ApiResult.Success<*>)
            val puzzle =
                ((result as ApiResult.Success<*>).data as CoachPlanResponse).todayPuzzle!!
            assertTrue(puzzle.solutionLineUci.isEmpty())
        }

    // ---------------------------------------------------------------------------
    // GET /puzzles/next — standalone puzzle trainer (docs/API_CONTRACTS.md §37)
    // ---------------------------------------------------------------------------

    private val puzzleNextLichessJson = """
{
  "puzzle_id": "lichess_AbCd1",
  "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
  "expected_move_uci": "g1f3",
  "solution_line_uci": ["g1f3", "b8c6", "f1c4"],
  "theme": "mix",
  "difficulty": "intermediate",
  "source": "lichess",
  "rating": 1400
}"""

    @Test
    fun `INT_PUZZLE_NEXT_METHOD_PATH - GET to puzzles next`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(puzzleNextLichessJson))
        client(token = "tok").getNextPuzzle()
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("GET", req.method)
        assertEquals("/puzzles/next", req.path)
    }

    @Test
    fun `INT_PUZZLE_NEXT_BEARER - Bearer sent, no X-Api-Key`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(puzzleNextLichessJson))
        client(token = "bearer-puzzle-tok").getNextPuzzle()
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer bearer-puzzle-tok", req.getHeader("Authorization"))
        assertNull(req.getHeader("X-Api-Key"))
    }

    @Test
    fun `INT_PUZZLE_NEXT_PARSED - lichess pick deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(puzzleNextLichessJson))
        val result = client(token = "tok").getNextPuzzle()
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as PuzzleNextDto
        assertEquals("lichess_AbCd1", data.puzzleId)
        assertEquals("g1f3", data.expectedMoveUci)
        assertEquals(listOf("g1f3", "b8c6", "f1c4"), data.solutionLineUci)
        assertEquals("mix", data.theme)
        assertEquals("intermediate", data.difficulty)
        assertEquals("lichess", data.source)
        assertEquals(1400, data.rating)
    }

    @Test
    fun `INT_PUZZLE_NEXT_LINE_ABSENT - legacy response defaults to an empty walk`() =
        runBlocking {
            // A server predating solution_line_uci must decode to an empty
            // list — the trainer then runs the single-move flow.
            val legacy = """
{
  "puzzle_id": "lichess_Old01",
  "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
  "expected_move_uci": "g1f3",
  "theme": "mix",
  "difficulty": "normal",
  "source": "lichess",
  "rating": 1200
}"""
            server.enqueue(MockResponse().setResponseCode(200).setBody(legacy))
            val result = client(token = "tok").getNextPuzzle()
            assertTrue(result is ApiResult.Success<*>)
            val data = (result as ApiResult.Success<*>).data as PuzzleNextDto
            assertTrue(data.solutionLineUci.isEmpty())
        }

    @Test
    fun `INT_PUZZLE_NEXT_LIBRARY_NULL_RATING - corpus pick with null rating parses`() =
        runBlocking {
            val libraryJson = """
{
  "puzzle_id": "fork_001",
  "fen": "8/8/8/8/8/4k3/4p3/4K3 b - - 0 1",
  "expected_move_uci": "e3d3",
  "theme": "fork",
  "difficulty": "beginner",
  "source": "library",
  "rating": null
}"""
            server.enqueue(MockResponse().setResponseCode(200).setBody(libraryJson))
            val result = client(token = "tok").getNextPuzzle()
            assertTrue(result is ApiResult.Success<*>)
            val data = (result as ApiResult.Success<*>).data as PuzzleNextDto
            assertEquals("fork_001", data.puzzleId)
            assertEquals("library", data.source)
            assertNull(data.rating)
        }

    @Test
    fun `INT_PUZZLE_NEXT_HTTP_ERROR - 503 maps to HttpError`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(503).setBody("""{"detail":"no puzzle available"}""")
        )
        val result = client(token = "tok").getNextPuzzle()
        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(503, (result as ApiResult.HttpError).code)
    }

    // ---------------------------------------------------------------------------
    // GET /game/history — source filter + limit (docs/API_CONTRACTS.md §7)
    // ---------------------------------------------------------------------------

    private val historyJson = """
{
  "games": [
    {"id":"g1","source":"lichess","result":"win","accuracy":0.61,"created_at":"2026-07-01T10:00:00"},
    {"id":"g2","source":"app","result":"loss","accuracy":0.55,"created_at":"2026-06-30T09:00:00"}
  ]
}"""

    @Test
    fun `INT_HISTORY_DEFAULT_PATH - no-arg call sends limit=20 and no source`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(historyJson))
        client(token = "tok").getGameHistory()
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("GET", req.method)
        assertEquals("/game/history?limit=20", req.path)
    }

    @Test
    fun `INT_HISTORY_SOURCE_PARAM - lichess filter serialised into the query`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(historyJson))
        client(token = "tok").getGameHistory(source = "lichess", limit = 50)
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("/game/history?limit=50&source=lichess", req.path)
    }

    @Test
    fun `INT_HISTORY_SOURCE_PARSED - source field deserialised per row`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(historyJson))
        val result = client(token = "tok").getGameHistory()
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        @Suppress("UNCHECKED_CAST")
        val games = (result as ApiResult.Success<*>).data as List<GameHistoryItem>
        assertEquals("lichess", games[0].source)
        assertEquals("app", games[1].source)
    }

    @Test
    fun `INT_HISTORY_SOURCE_DEFAULT - row without source decodes as app`() = runBlocking {
        // A payload from a server predating the source field must decode
        // as an in-app game, not throw.
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"games":[{"id":"g1","result":"win","accuracy":0.5,"created_at":"2026-07-01T10:00:00"}]}""",
            ),
        )
        val result = client(token = "tok").getGameHistory()
        @Suppress("UNCHECKED_CAST")
        val games = (result as ApiResult.Success<*>).data as List<GameHistoryItem>
        assertEquals("app", games[0].source)
    }

    @Test
    fun `INT_HISTORY_BEARER - Authorization Bearer sent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(historyJson))
        client(token = "bearer-hist-tok").getGameHistory()
        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer bearer-hist-tok", req.getHeader("Authorization"))
    }

    // ---------------------------------------------------------------------------
    // GET /game/{eventId}/positions — replay data incl. player_color
    // ---------------------------------------------------------------------------

    @Test
    fun `INT_POSITIONS_PATH - request path is game slash id slash positions`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"positions":[],"moves":[]}"""))
        client(token = "tok").getGamePositions("ev-3")
        assertEquals("/game/ev-3/positions", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `INT_POSITIONS_PLAYER_COLOR - player_color deserialised for a Black game`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"positions":["fen0","fen1"],"moves":["e4"],"player_color":"black"}""",
            ),
        )
        val result = client(token = "tok").getGamePositions("ev-1")
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as GamePositionsResponse
        assertEquals("black", data.playerColor)
        assertEquals(2, data.positions.size)
    }

    @Test
    fun `INT_POSITIONS_PLAYER_COLOR_NULL - missing player_color decodes to null`() = runBlocking {
        // A payload from a server predating the field must decode as null
        // (in-app / legacy => White, no flip), not throw.
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"positions":["fen0"],"moves":[]}"""),
        )
        val result = client(token = "tok").getGamePositions("ev-2")
        val data = (result as ApiResult.Success<*>).data as GamePositionsResponse
        assertNull(data.playerColor)
    }
}
