package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for the X-Auth-Token refresh path through the
 * client side.  Backend pins are in test_auth_refresh_header.py.
 *
 * Pinned invariants
 * -----------------
 *  1. SINK_INVOKED_ON_X_AUTH_TOKEN: when a 200 response carries
 *     X-Auth-Token, the sink lambda is invoked with the value.
 *  2. SINK_NOT_INVOKED_WHEN_HEADER_ABSENT: the sink is left alone
 *     when the response has no X-Auth-Token (older server / non-
 *     refreshing endpoint).
 *  3. SINK_NOT_INVOKED_WHEN_NULL: passing tokenSink=null is safe;
 *     no NPE, just no rotation.
 *  4. SINK_NOT_INVOKED_ON_FAILURE: 4xx / 5xx responses don't fire
 *     the sink even if the server somehow attached the header.
 *  5. CASE_INSENSITIVE_HEADER_LOOKUP: the lookup is robust against
 *     servers / proxies that lowercase the header name.
 */
class TokenRefreshIntegrationTest {

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

    private companion object {
        const val ME_OK_BODY = """
{
  "id": "player-abc-123",
  "email": "alice@chess.com",
  "rating": 1482.5,
  "confidence": 0.68
}"""
        const val OLD_TOKEN = "old-jwt-token"
        const val NEW_TOKEN = "rotated-jwt-token-from-server"
    }

    @Test
    fun `sink invoked when response carries X-Auth-Token header`() = runBlocking {
        val sink = AtomicReference<String?>(null)
        server.enqueue(
            MockResponse()
                .setBody(ME_OK_BODY)
                .setResponseCode(200)
                .addHeader("X-Auth-Token", NEW_TOKEN),
        )

        HttpAuthApiClient(
            baseUrl = baseUrl(),
            tokenSink = { sink.set(it) },
        ).me(OLD_TOKEN)

        // Make sure the request actually went through before checking.
        server.takeRequest(10, TimeUnit.SECONDS)
        assertEquals(
            "SINK_INVOKED_ON_X_AUTH_TOKEN — sink must receive the new JWT",
            NEW_TOKEN, sink.get(),
        )
    }

    @Test
    fun `sink not invoked when response lacks header`() = runBlocking {
        val sink = AtomicReference<String?>(null)
        // No X-Auth-Token header — older server, or a non-authenticated
        // route, or the server skipped the refresh for some reason.
        server.enqueue(
            MockResponse()
                .setBody(ME_OK_BODY)
                .setResponseCode(200),
        )

        HttpAuthApiClient(
            baseUrl = baseUrl(),
            tokenSink = { sink.set(it) },
        ).me(OLD_TOKEN)

        server.takeRequest(10, TimeUnit.SECONDS)
        assertNull(
            "SINK_NOT_INVOKED_WHEN_HEADER_ABSENT — sink must stay untouched",
            sink.get(),
        )
    }

    @Test
    fun `null sink is a safe no-op`() = runBlocking {
        // Null sink — clients that don't store tokens (test fakes,
        // unauthenticated paths) must not crash even when the server
        // includes the refresh header.
        server.enqueue(
            MockResponse()
                .setBody(ME_OK_BODY)
                .setResponseCode(200)
                .addHeader("X-Auth-Token", NEW_TOKEN),
        )

        // Should not throw.
        val result = HttpAuthApiClient(
            baseUrl = baseUrl(),
            tokenSink = null,
        ).me(OLD_TOKEN)

        server.takeRequest(10, TimeUnit.SECONDS)
        assertEquals(
            "ME response should still parse normally",
            true, result is ApiResult.Success,
        )
    }

    @Test
    fun `sink not invoked on 401 even if header present`() = runBlocking {
        val sink = AtomicReference<String?>(null)
        // Server SHOULDN'T attach X-Auth-Token to a 401, but if it
        // does (misconfiguration / old code path), the client must
        // not honor it — that would let an attacker harvest fresh
        // tokens by probing.
        server.enqueue(
            MockResponse()
                .setResponseCode(401)
                .addHeader("X-Auth-Token", "this-should-be-ignored")
                .setBody("""{"detail":"Invalid token"}"""),
        )

        HttpAuthApiClient(
            baseUrl = baseUrl(),
            tokenSink = { sink.set(it) },
        ).me(OLD_TOKEN)

        server.takeRequest(10, TimeUnit.SECONDS)
        assertNull(
            "SINK_NOT_INVOKED_ON_FAILURE — failure paths must not honour the header",
            sink.get(),
        )
    }

    @Test
    fun `sink invoked when header is lowercase x-auth-token`() = runBlocking {
        val sink = AtomicReference<String?>(null)
        // HttpURLConnection.getHeaderField is case-insensitive on
        // every JVM we run on, but pinning the contract here protects
        // against a future swap to a different HTTP client.
        server.enqueue(
            MockResponse()
                .setBody(ME_OK_BODY)
                .setResponseCode(200)
                .addHeader("x-auth-token", NEW_TOKEN),
        )

        HttpAuthApiClient(
            baseUrl = baseUrl(),
            tokenSink = { sink.set(it) },
        ).me(OLD_TOKEN)

        server.takeRequest(10, TimeUnit.SECONDS)
        assertEquals(NEW_TOKEN, sink.get())
    }

    // ── HttpCoachApiClient.chat — rounds out the refresh story ──

    @Test
    fun `coach chat success rotates the JWT via tokenSink`() = runBlocking {
        // Without this, a user who chats for 24h+ without ending a
        // game would silently lose their session.  The chat endpoint
        // is the most common authenticated call between game starts/
        // finishes, so it must participate in the refresh.
        val sink = AtomicReference<String?>(null)
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .addHeader("X-Auth-Token", NEW_TOKEN)
                .setBody(
                    """
                    {"text":"Try Nf3","engine_signal":null,"mode":"CHAT_V1"}
                    """.trimIndent(),
                ),
        )

        val client = HttpCoachApiClient(
            baseUrl = baseUrl(),
            apiKey = "test-key",
            tokenProvider = { OLD_TOKEN },
            tokenSink = { sink.set(it) },
        )
        client.chat(
            fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            messages = listOf(ChatMessageDto(role = "user", content = "What now?")),
            playerProfile = null,
            pastMistakes = null,
            moveCount = null,
        )

        server.takeRequest(10, TimeUnit.SECONDS)
        assertEquals(NEW_TOKEN, sink.get())
    }
}
