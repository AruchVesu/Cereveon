package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.toList
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
 * Integration tests for [HttpCoachApiClient.chatStream] against a real local HTTP server.
 *
 * Uses OkHttp MockWebServer to serve canned SSE responses and verify that the
 * production client correctly parses the SSE wire format and emits the expected
 * [StreamChunk] items.
 *
 * Contract reference: POST /chat/stream — same auth contract as /chat.
 *
 * Invariants pinned
 * -----------------
 *  1. STREAM_METHOD          request uses HTTP POST.
 *  2. STREAM_PATH            request path is /chat/stream.
 *  3. STREAM_ACCEPT_HEADER   Accept: text/event-stream header is sent.
 *  4. STREAM_CHUNK_EMISSION  chunk events emitted as [StreamChunk.Chunk] items.
 *  5. STREAM_DONE_EMISSION   done event emitted as exactly one [StreamChunk.Done].
 *  6. STREAM_FULL_TEXT       concatenated [StreamChunk.Chunk.text] equals the full reply.
 *  7. STREAM_ENGINE_SIGNAL   [StreamChunk.Done.engineSignal] is populated from done event.
 *  8. STREAM_HTTP_ERROR      non-200 response emits [StreamChunk.StreamError] with code.
 */
class ChatStreamApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    private val startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    private val apiKey = "stream-integration-key"

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

    private fun client(): HttpCoachApiClient = HttpCoachApiClient(
        baseUrl = baseUrl(),
        apiKey = apiKey,
    )

    // ---------------------------------------------------------------------------
    // Shared SSE fixture
    // ---------------------------------------------------------------------------

    companion object {
        private const val STUB_REPLY = "Engine shows equality."

        // SSE body: one chunk per word, then a done event.
        private val SSE_BODY: String = buildString {
            val words = STUB_REPLY.split(" ")
            for ((i, word) in words.withIndex()) {
                val text = if (i == words.size - 1) word else "$word "
                append("data: {\"type\":\"chunk\",\"text\":\"$text\"}\n\n")
            }
            append(
                "data: {\"type\":\"done\"," +
                    "\"engine_signal\":{" +
                    "\"evaluation\":{\"band\":\"equal\",\"side\":\"white\"}," +
                    "\"eval_delta\":\"stable\"," +
                    "\"last_move_quality\":\"unknown\"," +
                    "\"tactical_flags\":[]," +
                    "\"position_flags\":[]," +
                    "\"phase\":\"opening\"}," +
                    "\"mode\":\"CHAT_V1\"}\n\n",
            )
        }
    }

    private fun enqueueSse() {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .addHeader("Content-Type", "text/event-stream")
                .setBody(SSE_BODY),
        )
    }

    // ---------------------------------------------------------------------------
    // 1–3  HTTP wire properties
    // ---------------------------------------------------------------------------

    @Test
    fun `STREAM_METHOD - request uses HTTP POST`() = runBlocking {
        enqueueSse()
        client().chatStream(startingFen, emptyList()).toList()
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `STREAM_PATH - request path is slash chat slash stream`() = runBlocking {
        enqueueSse()
        client().chatStream(startingFen, emptyList()).toList()
        assertEquals("/chat/stream", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `STREAM_ACCEPT_HEADER - Accept text slash event-stream header is sent`() = runBlocking {
        enqueueSse()
        client().chatStream(startingFen, emptyList()).toList()
        val accept = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Accept") ?: ""
        assertTrue(
            "Accept header must contain text/event-stream, was: $accept",
            "text/event-stream" in accept,
        )
    }

    // ---------------------------------------------------------------------------
    // 4–7  Flow item types and content
    // ---------------------------------------------------------------------------

    @Test
    fun `STREAM_CHUNK_EMISSION - chunk events appear as StreamChunk Chunk items`() = runBlocking {
        enqueueSse()
        val items = client().chatStream(startingFen, emptyList()).toList()
        val chunks = items.filterIsInstance<StreamChunk.Chunk>()
        assertTrue("Expected at least one StreamChunk.Chunk, got: $items", chunks.isNotEmpty())
    }

    @Test
    fun `STREAM_DONE_EMISSION - done event appears as exactly one StreamChunk Done`() = runBlocking {
        enqueueSse()
        val items = client().chatStream(startingFen, emptyList()).toList()
        val done = items.filterIsInstance<StreamChunk.Done>()
        assertEquals("Expected exactly one StreamChunk.Done, got: $items", 1, done.size)
    }

    @Test
    fun `STREAM_FULL_TEXT - concatenated chunk text equals full reply`() = runBlocking {
        enqueueSse()
        val items = client().chatStream(startingFen, emptyList()).toList()
        val full = items.filterIsInstance<StreamChunk.Chunk>().joinToString("") { it.text }
        assertEquals("Concatenated chunks must equal the full reply", STUB_REPLY, full)
    }

    @Test
    fun `STREAM_ENGINE_SIGNAL - Done engineSignal is populated from done event`() = runBlocking {
        enqueueSse()
        val items = client().chatStream(startingFen, emptyList()).toList()
        val done = items.filterIsInstance<StreamChunk.Done>().first()
        assertNotNull("Done.engineSignal must not be null", done.engineSignal)
        assertEquals("equal", done.engineSignal!!.evaluation?.band)
        assertEquals("opening", done.engineSignal.phase)
    }

    // ---------------------------------------------------------------------------
    // 8  HTTP error → StreamError
    // ---------------------------------------------------------------------------

    @Test
    fun `STREAM_HTTP_ERROR - non-200 response emits StreamChunk StreamError with code`() =
        runBlocking {
            server.enqueue(
                MockResponse()
                    .setResponseCode(401)
                    .setBody("{\"detail\":\"Unauthorized\"}"),
            )
            val items = client().chatStream(startingFen, emptyList()).toList()
            val errors = items.filterIsInstance<StreamChunk.StreamError>()
            assertTrue(
                "Expected StreamChunk.StreamError for HTTP 401, got: $items",
                errors.isNotEmpty(),
            )
            assertTrue(
                "StreamError.message must mention 401, was: ${errors.first().message}",
                "401" in errors.first().message,
            )
        }
}
