package com.cereveon.myapp

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * X-API-Version header coverage across every coaching API client.
 *
 * The server enforces version mismatch with HTTP 400 (see
 * `llm/tests/test_api_version_header.py` for the server-side
 * contract).  The client must send the header on every request
 * for the contract to bind, including auth endpoints (where the
 * header isn't enforced) — sending it everywhere keeps a single
 * code path and lets a future Phase 2 strict-on-missing flip ship
 * without per-endpoint exceptions.
 *
 * Stable test IDs (do NOT rename):
 *   AVH_CLIENT_GAME_*       — every endpoint on HttpGameApiClient
 *   AVH_CLIENT_AUTH_*       — every endpoint on HttpAuthApiClient
 *   AVH_CLIENT_LIVEMOVE_*   — POST /live/move
 *   AVH_CLIENT_ENGINE_EVAL  — POST /engine/eval
 *   AVH_CLIENT_COACH_CHAT   — POST /chat
 *   AVH_CLIENT_COACH_FEEDBACK — POST /chat/feedback
 *   AVH_CLIENT_SEND_FEEDBACK — POST /feedback (product-feedback form)
 *   AVH_CLIENT_CONST        — COACH_API_VERSION matches server "1"
 */
class ApiVersionHeaderTest {

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

    private fun assertVersionHeader(req: okhttp3.mockwebserver.RecordedRequest?) {
        check(req != null) { "no request received from MockWebServer within 10s" }
        assertEquals(
            "X-API-Version header missing or mismatched on ${req.method} ${req.path}",
            COACH_API_VERSION,
            req.getHeader(COACH_API_VERSION_HEADER),
        )
    }

    // ----------------------------------------------------------------------
    // Sanity check — the constant matches the server's API_VERSION.
    // The server-side test (AVH_01 in llm/tests/test_api_version_header.py)
    // pins the same value; mismatch here = a release that bumped one side
    // without the other.
    // ----------------------------------------------------------------------

    @Test
    fun `AVH_CLIENT_CONST coach api version constant equals 2`() {
        assertEquals(
            "COACH_API_VERSION must equal '2' until the next deliberate bump. " +
                "Bumped 1 -> 2 in the Lichess v2 async-import PR (POST " +
                "/lichess/import switches from synchronous 200 + summary " +
                "to 202 + job payload).  Server-side sibling: AVH_01 in " +
                "llm/tests/test_api_version_header.py.  '1' remains in " +
                "the server's API_VERSIONS_SUPPORTED tuple for backward " +
                "compat with shipped v1 builds; this constant is the " +
                "preferred / current version a fresh build targets.",
            "2",
            COACH_API_VERSION,
        )
        assertEquals("X-API-Version", COACH_API_VERSION_HEADER)
    }

    // ----------------------------------------------------------------------
    // GameApiClient — uses two centralized helpers (openConnection POST,
    // openGetConnection GET).  We test one POST and one GET to exercise
    // both helpers; if either is missing the header, both endpoints fail.
    // ----------------------------------------------------------------------

    @Test
    fun `AVH_CLIENT_GAME_POST start game sends X-API-Version`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"game_id":"g","status":"ok"}"""))
        val client = HttpGameApiClient(baseUrl = baseUrl(), apiKey = "k")
        client.startGame("p1")
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    @Test
    fun `AVH_CLIENT_GAME_GET seca status sends X-API-Version`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"safe_mode": true}"""))
        val client = HttpGameApiClient(baseUrl = baseUrl(), apiKey = "k")
        client.getSecaStatus()
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    // ----------------------------------------------------------------------
    // AuthApiClient — six endpoints, each with its own setup block.
    // We exercise login + me + register because they cover the three
    // distinct request shapes (POST with body, GET with bearer, POST
    // with body but no bearer).  changePassword, updateMe, logout are
    // structurally similar to login.
    // ----------------------------------------------------------------------

    @Test
    fun `AVH_CLIENT_AUTH_LOGIN sends X-API-Version`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"access_token":"t","player_id":"p","token_type":"bearer"}"""),
        )
        val client = HttpAuthApiClient(baseUrl = baseUrl())
        client.login("a@b.c", "pw1234567")
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    @Test
    fun `AVH_CLIENT_AUTH_ME sends X-API-Version`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"id":"p","email":"a@b.c","rating":1500.0,"confidence":0.7}"""),
        )
        val client = HttpAuthApiClient(baseUrl = baseUrl())
        client.me("token-x")
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    @Test
    fun `AVH_CLIENT_AUTH_REGISTER sends X-API-Version`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(201)
                .setBody("""{"access_token":"t","player_id":"p","token_type":"bearer"}"""),
        )
        val client = HttpAuthApiClient(baseUrl = baseUrl())
        client.register("a@b.c", "pw1234567")
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    // ----------------------------------------------------------------------
    // LiveMoveApiClient — single endpoint POST /live/move.
    // ----------------------------------------------------------------------

    @Test
    fun `AVH_CLIENT_LIVEMOVE sends X-API-Version`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"status":"ok","hint":"fine","move_quality":"GOOD","mode":"LIVE_V1"}"""),
        )
        val client = HttpLiveMoveClient(baseUrl = baseUrl(), apiKey = "k", tokenProvider = { null })
        client.getLiveCoaching(
            fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            uci = "e2e4",
        )
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    // ----------------------------------------------------------------------
    // EngineEvalApiClient — single endpoint POST /engine/eval.
    // ----------------------------------------------------------------------

    @Test
    fun `AVH_CLIENT_ENGINE_EVAL sends X-API-Version`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"score":12,"best_move":"e2e4","source":"engine"}"""),
        )
        val client = HttpEngineEvalClient(baseUrl = baseUrl(), apiKey = "k")
        client.evaluate("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    // ----------------------------------------------------------------------
    // CoachApiClient — POST /chat (non-stream), POST /chat/feedback.
    // /chat/stream is exercised separately by ChatStreamApiClientIntegrationTest;
    // we add a dedicated stream check here to confirm the header lands on
    // the SSE request too.
    // ----------------------------------------------------------------------

    @Test
    fun `AVH_CLIENT_COACH_CHAT sends X-API-Version`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"reply":"...","session_id":"s","engine_signal":null,"mode":"CHAT_V1"}""",
                ),
        )
        val client = HttpCoachApiClient(baseUrl = baseUrl(), apiKey = "k")
        client.chat(
            fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            messages = listOf(ChatMessageDto(role = "user", content = "Why?")),
            playerProfile = null,
            pastMistakes = null,
            moveCount = null,
            coachVoice = null,
        )
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    @Test
    fun `AVH_CLIENT_COACH_STREAM sends X-API-Version`() = runBlocking {
        // Even an empty / quickly-closed SSE response is enough to verify
        // the header was set on the request.
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"type\":\"done\"}\n\n"),
        )
        val client = HttpCoachApiClient(baseUrl = baseUrl(), apiKey = "k")
        // Drain the flow so the underlying request actually fires; we
        // don't care about the chunks themselves for this header check.
        client.chatStream(
            fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            messages = listOf(ChatMessageDto(role = "user", content = "Why?")),
            playerProfile = null,
            pastMistakes = null,
            moveCount = null,
            coachVoice = null,
        ).first()
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    @Test
    fun `AVH_CLIENT_COACH_FEEDBACK sends X-API-Version`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        val client = HttpCoachApiClient(baseUrl = baseUrl(), apiKey = "k")
        client.submitFeedback(
            fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            isHelpful = true,
            token = null,
        )
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }

    // ----------------------------------------------------------------------
    // FeedbackApiClient — single endpoint POST /feedback (the drawer's
    // product-feedback form; distinct from CoachApiClient's per-position
    // thumbs up/down above).
    // ----------------------------------------------------------------------

    @Test
    fun `AVH_CLIENT_SEND_FEEDBACK sends X-API-Version`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"status":"received","id":"row-1"}"""),
        )
        val client = HttpFeedbackApiClient(
            baseUrl = baseUrl(),
            apiKey = "k",
            tokenProvider = { null },
        )
        client.submitFeedback(message = "great app", appVersion = "1.0")
        assertVersionHeader(server.takeRequest(10, TimeUnit.SECONDS))
    }
}
