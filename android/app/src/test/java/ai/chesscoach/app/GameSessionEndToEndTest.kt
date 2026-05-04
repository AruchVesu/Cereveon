package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * End-to-end scenario test for the complete game session lifecycle.
 *
 * Simulates the Android client flow documented in docs/API_CONTRACTS.md:
 *
 *   1. POST /game/start       → obtain game_id
 *   2. POST /engine/eval      → AI move evaluation (score + best_move)
 *   3. POST /game/finish      → submit completed game, receive coaching response
 *   4. POST /chat             → open coaching chat with profile from finish response
 *
 * Uses OkHttp MockWebServer to serve all four canned responses over a real
 * local HTTP port.  No live backend, Stockfish, or network required.
 *
 * All four clients (HttpGameApiClient, HttpEngineEvalClient, HttpCoachApiClient)
 * point at the same MockWebServer port.  Responses are served FIFO in the order
 * they are enqueued.
 *
 * Invariants pinned
 * -----------------
 *  1.  E2E_GAME_ID_PROPAGATED      game_id from /game/start is non-empty.
 *  2.  E2E_EVAL_SCORE_AVAILABLE    score from /engine/eval is non-null and parsed.
 *  3.  E2E_EVAL_BEST_MOVE          best_move from /engine/eval is non-null.
 *  4.  E2E_FINISH_STATUS_STORED    status from /game/finish equals "stored".
 *  5.  E2E_COACH_ACTION_NONNULL    coach_action is present in finish response.
 *  6.  E2E_PROFILE_FROM_FINISH     PlayerProfileDto built from finish response fields.
 *  7.  E2E_CHAT_REPLY_NONNULL      chat reply is a non-empty string.
 *  8.  E2E_REQUEST_ORDER           requests arrive at server in correct sequence.
 */
class GameSessionEndToEndTest {

    private lateinit var server: MockWebServer

    private val apiKey = "e2e-test-key"
    private val playerId = "player-e2e-1"
    private val startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    private val fenAfterE4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"

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

    // ---------------------------------------------------------------------------
    // Canned response bodies — match docs/API_CONTRACTS.md schemas
    // ---------------------------------------------------------------------------

    private val gameStartBody = """{"game_id": "e2e-game-001"}"""

    private val engineEvalBody = """
{
  "score": -18,
  "best_move": "d7d5",
  "source": "engine",
  "_metrics": {"cache_hit": false, "engine_time_ms": 45}
}"""

    private val gameFinishBody = """
{
  "status": "stored",
  "new_rating": 1488.0,
  "confidence": 0.65,
  "learning": {"status": "updated"},
  "coach_action": {
    "type": "NONE",
    "weakness": null,
    "reason": "Stable progress — no intervention required."
  },
  "coach_content": {
    "title": "Keep playing",
    "description": "You are making steady progress.",
    "payload": {}
  }
}"""

    private val chatBody = """
{
  "reply": "Engine: black has equal [opening]. Keep developing your pieces.",
  "engine_signal": {
    "evaluation": {"band": "equal", "side": "black"},
    "eval_delta": "stable",
    "last_move_quality": "unknown",
    "tactical_flags": [],
    "position_flags": [],
    "phase": "opening"
  },
  "mode": "CHAT_V1"
}"""

    // ---------------------------------------------------------------------------
    // Full lifecycle helper — enqueues all four responses, runs all four calls,
    // returns the collected results and captured requests.
    // ---------------------------------------------------------------------------

    private data class LifecycleResult(
        val gameId: String,
        val score: Int?,
        val bestMove: String?,
        val finishResponse: GameFinishResponse,
        val chatReply: String,
        val requestPaths: List<String>,
    )

    private fun runLifecycle(): LifecycleResult = runBlocking {
        // Enqueue all four responses — MockWebServer serves them FIFO
        server.enqueue(MockResponse().setResponseCode(200).setBody(gameStartBody))
        server.enqueue(MockResponse().setResponseCode(200).setBody(engineEvalBody))
        server.enqueue(MockResponse().setResponseCode(200).setBody(gameFinishBody))
        server.enqueue(MockResponse().setResponseCode(200).setBody(chatBody))

        val gameClient = HttpGameApiClient(baseUrl = baseUrl(), apiKey = apiKey)
        val evalClient = HttpEngineEvalClient(baseUrl = baseUrl())
        val chatClient = HttpCoachApiClient(baseUrl = baseUrl(), apiKey = apiKey)

        // Step 1 — Start game
        val startResult = gameClient.startGame(playerId)
        assertTrue("startGame must succeed", startResult is ApiResult.Success<*>)
        val gameId = ((startResult as ApiResult.Success<*>).data as GameStartResponse).gameId

        // Step 2 — AI move evaluation after human plays e2e4
        val evalResult = evalClient.evaluate(fenAfterE4)
        assertTrue("evaluate must succeed", evalResult is ApiResult.Success<*>)
        val eval = (evalResult as ApiResult.Success<*>).data as EngineEvalResponse

        // Step 3 — Finish game
        val finishReq = GameFinishRequest(
            pgn = "1.e4",
            result = "draw",
            accuracy = 0.78f,
            weaknesses = mapOf("tactics" to 0.4f),
            playerId = playerId,
        )
        val finishResult = gameClient.finishGame(finishReq)
        assertTrue("finishGame must succeed", finishResult is ApiResult.Success<*>)
        val finishResponse = (finishResult as ApiResult.Success<*>).data as GameFinishResponse

        // Step 4 — Open coaching chat using player profile derived from finish response
        val profile = PlayerProfileDto(
            rating = finishResponse.newRating,
            confidence = finishResponse.confidence,
        )
        val chatResult = chatClient.chat(
            fen = fenAfterE4,
            messages = listOf(ChatMessageDto("user", "How did I do?")),
            playerProfile = profile,
        )
        assertTrue("chat must succeed", chatResult is ApiResult.Success<*>)
        val chatReply = ((chatResult as ApiResult.Success<*>).data as ChatResponseBody).reply

        // Collect the request paths in arrival order
        val paths = (1..4).map {
            requireNotNull(server.takeRequest(10, TimeUnit.SECONDS)) {
                "MockWebServer received no request $it within 10 s"
            }.path ?: ""
        }

        LifecycleResult(
            gameId = gameId,
            score = eval.score,
            bestMove = eval.bestMove,
            finishResponse = finishResponse,
            chatReply = chatReply,
            requestPaths = paths,
        )
    }

    // ---------------------------------------------------------------------------
    // Invariant tests — each calls runLifecycle() independently
    // ---------------------------------------------------------------------------

    @Test
    fun `E2E_GAME_ID_PROPAGATED - game_id from game start is non-empty`() {
        val r = runLifecycle()
        assertTrue("game_id must be non-empty, was: '${r.gameId}'", r.gameId.isNotEmpty())
        assertEquals("e2e-game-001", r.gameId)
    }

    @Test
    fun `E2E_EVAL_SCORE_AVAILABLE - score from engine eval is non-null and parsed`() {
        val r = runLifecycle()
        assertNotNull("Engine eval score must not be null", r.score)
        assertEquals(-18, r.score)
    }

    @Test
    fun `E2E_EVAL_BEST_MOVE - best_move from engine eval is non-null`() {
        val r = runLifecycle()
        assertNotNull("best_move must not be null", r.bestMove)
        assertEquals("d7d5", r.bestMove)
    }

    @Test
    fun `E2E_FINISH_STATUS_STORED - game finish status equals stored`() {
        val r = runLifecycle()
        assertEquals("stored", r.finishResponse.status)
    }

    @Test
    fun `E2E_COACH_ACTION_NONNULL - coach_action is present in finish response`() {
        val r = runLifecycle()
        assertNotNull("coachAction must not be null", r.finishResponse.coachAction)
        assertEquals("NONE", r.finishResponse.coachAction.type)
    }

    @Test
    fun `E2E_PROFILE_FROM_FINISH - PlayerProfileDto built from finish response fields`() {
        val r = runLifecycle()
        // new_rating and confidence from /game/finish feed into the /chat request
        val profile = PlayerProfileDto(
            rating = r.finishResponse.newRating,
            confidence = r.finishResponse.confidence,
        )
        assertEquals(1488.0f, profile.rating, 0.01f)
        assertEquals(0.65f, profile.confidence, 0.001f)
    }

    @Test
    fun `E2E_CHAT_REPLY_NONNULL - chat reply is a non-empty string`() {
        val r = runLifecycle()
        assertTrue("chat reply must be non-empty, was: '${r.chatReply}'",
            r.chatReply.isNotEmpty())
    }

    @Test
    fun `E2E_REQUEST_ORDER - requests arrive at server in correct lifecycle sequence`() {
        val r = runLifecycle()
        // Requests must arrive in the documented order
        assertEquals("/game/start", r.requestPaths[0])
        assertEquals("/engine/eval", r.requestPaths[1])
        assertEquals("/game/finish", r.requestPaths[2])
        assertEquals("/chat", r.requestPaths[3])
    }
}
