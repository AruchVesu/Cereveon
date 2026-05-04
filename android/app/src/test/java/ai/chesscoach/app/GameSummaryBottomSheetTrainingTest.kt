package ai.chesscoach.app

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Integration tests for the GET /next-training/{player_id} fetch that
 * [GameSummaryBottomSheet] performs after a game ends.
 *
 * Uses [MockWebServer] to verify the HTTP layer.  The formatting helpers
 * (formatTopic, formatGain, difficultyProgress) are tested in
 * [GameSummaryBottomSheetTest]; only the network/parsing contract is
 * verified here.
 *
 * Invariants pinned
 * -----------------
 *  1.  TRAINING_REQUEST_PATH:       GET /next-training/{playerId} uses the correct URL path.
 *  2.  TRAINING_REQUEST_METHOD:     Request method is GET.
 *  3.  TRAINING_RESPONSE_TOPIC:     topic field is parsed correctly.
 *  4.  TRAINING_RESPONSE_DIFFICULTY: difficulty (float) is parsed correctly.
 *  5.  TRAINING_RESPONSE_FORMAT:    format field is parsed correctly.
 *  6.  TRAINING_RESPONSE_GAIN:      expected_gain is parsed as expectedGain.
 *  7.  TRAINING_404_HTTP_ERROR:     HTTP 404 → ApiResult.HttpError(404).
 *  8.  TRAINING_500_HTTP_ERROR:     HTTP 500 → ApiResult.HttpError(500).
 *  9.  TRAINING_MISSING_FIELDS:     Partial JSON → defaults used, no crash.
 * 10.  TRAINING_API_KEY_HEADER:     X-Api-Key header is present in the request.
 */
class GameSummaryBottomSheetTrainingTest {

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

    private fun client() = HttpGameApiClient(
        baseUrl = "http://127.0.0.1:${server.port}",
        apiKey = "test-key",
    )

    private val fullTrainingJson = """
        {
            "topic": "endgame_technique",
            "difficulty": 0.7,
            "format": "puzzle",
            "expected_gain": 14.0
        }
    """.trimIndent()

    // ------------------------------------------------------------------
    // 1. Request uses the correct URL path
    // ------------------------------------------------------------------

    @Test
    fun `getNextTraining uses correct path with player id`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullTrainingJson))

        client().getNextTraining("player-abc")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("/next-training/player-abc", req.path)
    }

    // ------------------------------------------------------------------
    // 2. Request method is GET
    // ------------------------------------------------------------------

    @Test
    fun `getNextTraining uses GET method`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullTrainingJson))

        client().getNextTraining("p1")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("GET", req.method)
    }

    // ------------------------------------------------------------------
    // 3–6  Full response parsed correctly
    // ------------------------------------------------------------------

    @Test
    fun `getNextTraining parses topic correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullTrainingJson))

        val result = client().getNextTraining("p1") as ApiResult.Success
        assertEquals("endgame_technique", result.data.topic)
    }

    @Test
    fun `getNextTraining parses difficulty correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullTrainingJson))

        val result = client().getNextTraining("p1") as ApiResult.Success
        assertEquals(0.7f, result.data.difficulty, 0.001f)
    }

    @Test
    fun `getNextTraining parses format correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullTrainingJson))

        val result = client().getNextTraining("p1") as ApiResult.Success
        assertEquals("puzzle", result.data.format)
    }

    @Test
    fun `getNextTraining parses expected_gain as expectedGain`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullTrainingJson))

        val result = client().getNextTraining("p1") as ApiResult.Success
        assertEquals(14.0f, result.data.expectedGain, 0.001f)
    }

    // ------------------------------------------------------------------
    // 7–8  Non-200 responses → HttpError (graceful empty state)
    // ------------------------------------------------------------------

    @Test
    fun `getNextTraining returns HttpError on 404`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404))

        val result = client().getNextTraining("unknown-player")

        assertTrue("Expected HttpError for 404", result is ApiResult.HttpError)
        assertEquals(404, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `getNextTraining returns HttpError on 500`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500))

        val result = client().getNextTraining("p1")

        assertTrue("Expected HttpError for 500", result is ApiResult.HttpError)
        assertEquals(500, (result as ApiResult.HttpError).code)
    }

    // ------------------------------------------------------------------
    // 9  Partial JSON → defaults used, no crash
    // ------------------------------------------------------------------

    @Test
    fun `getNextTraining handles partial JSON response with defaults`() = runBlocking {
        // Only topic present; other fields fall back to defaults in parseTrainingResponse
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"topic":"tactics"}"""))

        val result = client().getNextTraining("p1") as ApiResult.Success
        assertEquals("tactics", result.data.topic)
        assertEquals(0.5f, result.data.difficulty, 0.001f)   // default
        assertEquals("", result.data.format)                   // default
        assertEquals(0.0f, result.data.expectedGain, 0.001f)  // default
    }

    // ------------------------------------------------------------------
    // 10  X-Api-Key header present
    // ------------------------------------------------------------------

    @Test
    fun `getNextTraining sends X-Api-Key header`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullTrainingJson))

        HttpGameApiClient(
            baseUrl = "http://127.0.0.1:${server.port}",
            apiKey = "my-api-key",
        ).getNextTraining("p1")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("my-api-key", req.getHeader("X-Api-Key"))
    }
}
