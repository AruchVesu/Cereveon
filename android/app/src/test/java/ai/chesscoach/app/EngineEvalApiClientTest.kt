package ai.chesscoach.app

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the engine evaluation API client abstraction layer.
 *
 * Covers:
 *  - [EngineEvalRequest] data class
 *  - [EngineEvalResponse] data class
 *  - [EngineEvalClient] interface contract via [FakeEngineEvalClient]
 *  - [HttpEngineEvalClient] constructor properties
 *
 * [HttpEngineEvalClient] network I/O is not tested here; it is exercised in
 * integration / instrumented tests against a live or test-double server.
 *
 * Invariants pinned
 * -----------------
 *  1.  REQUEST_FEN:                   EngineEvalRequest retains fen.
 *  2.  REQUEST_EQUALITY:              Two identical EngineEvalRequests are equal.
 *  3.  REQUEST_INEQUALITY:            EngineEvalRequests differ when fen differs.
 *  4.  RESPONSE_SCORE_INT:            EngineEvalResponse retains integer score.
 *  5.  RESPONSE_SCORE_NULL:           EngineEvalResponse accepts null score.
 *  6.  RESPONSE_BEST_MOVE_STRING:     EngineEvalResponse retains bestMove string.
 *  7.  RESPONSE_BEST_MOVE_NULL:       EngineEvalResponse accepts null bestMove.
 *  8.  RESPONSE_SOURCE:               EngineEvalResponse retains source field.
 *  9.  RESPONSE_EQUALITY:             Two identical EngineEvalResponses are equal.
 * 10.  RESPONSE_INEQUALITY_SCORE:     Responses differ when score differs.
 * 11.  RESPONSE_INEQUALITY_BEST_MOVE: Responses differ when bestMove differs.
 * 12.  RESPONSE_INEQUALITY_SOURCE:    Responses differ when source differs.
 * 13.  FAKE_SUCCESS:                  FakeEngineEvalClient returns configured success result.
 * 14.  FAKE_HTTP_ERROR:               FakeEngineEvalClient returns HttpError.
 * 15.  FAKE_TIMEOUT:                  FakeEngineEvalClient returns Timeout.
 * 16.  FAKE_NETWORK_ERROR:            FakeEngineEvalClient returns NetworkError.
 * 17.  FAKE_CALL_COUNT:               FakeEngineEvalClient counts evaluate calls.
 * 18.  FAKE_LAST_FEN:                 FakeEngineEvalClient records last evaluated fen.
 * 19.  FAKE_NULL_SCORE_PROPAGATED:    Null score propagates through fake client.
 * 20.  FAKE_NULL_BEST_MOVE_PROPAGATED: Null bestMove propagates through fake client.
 * 21.  HTTP_CLIENT_BASE_URL:          HttpEngineEvalClient stores baseUrl.
 * 22.  HTTP_CLIENT_DEFAULTS:          HttpEngineEvalClient has correct default timeouts.
 * 23.  HTTP_CLIENT_CONNECT_TIMEOUT:   HttpEngineEvalClient stores connectTimeoutMs.
 * 24.  HTTP_CLIENT_READ_TIMEOUT:      HttpEngineEvalClient stores readTimeoutMs.
 * 25.  SOURCE_ENUM_ENGINE:            "engine" is a valid source value.
 * 26.  SOURCE_ENUM_CACHE:             "cache" is a valid source value.
 * 27.  SOURCE_ENUM_BOOK:              "book" is a valid source value.
 */
class EngineEvalApiClientTest {

    // ------------------------------------------------------------------
    // Test double
    // ------------------------------------------------------------------

    private class FakeEngineEvalClient(
        var nextResult: ApiResult<EngineEvalResponse> =
            ApiResult.Success(
                EngineEvalResponse(score = 42, bestMove = "e2e4", source = "engine")
            ),
    ) : EngineEvalClient {
        var callCount = 0
        var lastFen: String? = null

        override suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse> {
            callCount++
            lastFen = fen
            return nextResult
        }
    }

    // ------------------------------------------------------------------
    // 1–3  EngineEvalRequest
    // ------------------------------------------------------------------

    @Test fun `EngineEvalRequest retains fen`() {
        val req = EngineEvalRequest(fen = "startpos")
        assertEquals("startpos", req.fen)
    }

    @Test fun `two identical EngineEvalRequests are equal`() {
        val a = EngineEvalRequest("startpos")
        val b = EngineEvalRequest("startpos")
        assertEquals(a, b)
    }

    @Test fun `EngineEvalRequests differ when fen differs`() {
        val a = EngineEvalRequest("startpos")
        val b = EngineEvalRequest("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 4–12  EngineEvalResponse
    // ------------------------------------------------------------------

    @Test fun `EngineEvalResponse retains integer score`() {
        val resp = EngineEvalResponse(score = 152, bestMove = "e2e4", source = "engine")
        assertEquals(152, resp.score)
    }

    @Test fun `EngineEvalResponse accepts null score`() {
        val resp = EngineEvalResponse(score = null, bestMove = null, source = "engine")
        assertNull(resp.score)
    }

    @Test fun `EngineEvalResponse retains bestMove string`() {
        val resp = EngineEvalResponse(score = 50, bestMove = "d2d4", source = "cache")
        assertEquals("d2d4", resp.bestMove)
    }

    @Test fun `EngineEvalResponse accepts null bestMove`() {
        val resp = EngineEvalResponse(score = 0, bestMove = null, source = "engine")
        assertNull(resp.bestMove)
    }

    @Test fun `EngineEvalResponse retains source field`() {
        for (source in listOf("engine", "cache", "book")) {
            val resp = EngineEvalResponse(score = 0, bestMove = null, source = source)
            assertEquals(source, resp.source)
        }
    }

    @Test fun `two identical EngineEvalResponses are equal`() {
        val a = EngineEvalResponse(score = 100, bestMove = "e2e4", source = "engine")
        val b = EngineEvalResponse(score = 100, bestMove = "e2e4", source = "engine")
        assertEquals(a, b)
    }

    @Test fun `EngineEvalResponses differ when score differs`() {
        val a = EngineEvalResponse(score = 100, bestMove = "e2e4", source = "engine")
        val b = EngineEvalResponse(score = 200, bestMove = "e2e4", source = "engine")
        assertNotEquals(a, b)
    }

    @Test fun `EngineEvalResponses differ when bestMove differs`() {
        val a = EngineEvalResponse(score = 50, bestMove = "e2e4", source = "engine")
        val b = EngineEvalResponse(score = 50, bestMove = "d2d4", source = "engine")
        assertNotEquals(a, b)
    }

    @Test fun `EngineEvalResponses differ when source differs`() {
        val a = EngineEvalResponse(score = 50, bestMove = "e2e4", source = "engine")
        val b = EngineEvalResponse(score = 50, bestMove = "e2e4", source = "cache")
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 13–20  FakeEngineEvalClient
    // ------------------------------------------------------------------

    @Test fun `FakeEngineEvalClient returns configured success result`() = runBlocking {
        val resp = EngineEvalResponse(score = 152, bestMove = "e2e4", source = "engine")
        val fake = FakeEngineEvalClient(nextResult = ApiResult.Success(resp))
        val result = fake.evaluate("startpos")
        assertTrue(result is ApiResult.Success)
        assertEquals(152, (result as ApiResult.Success).data.score)
        assertEquals("e2e4", result.data.bestMove)
    }

    @Test fun `FakeEngineEvalClient returns HttpError`() = runBlocking {
        val fake = FakeEngineEvalClient(nextResult = ApiResult.HttpError(503))
        val result = fake.evaluate("startpos")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(503, (result as ApiResult.HttpError).code)
    }

    @Test fun `FakeEngineEvalClient returns Timeout`() = runBlocking {
        val fake = FakeEngineEvalClient(nextResult = ApiResult.Timeout)
        val result = fake.evaluate("startpos")
        assertSame(ApiResult.Timeout, result)
    }

    @Test fun `FakeEngineEvalClient returns NetworkError`() = runBlocking {
        val cause = RuntimeException("connection refused")
        val fake = FakeEngineEvalClient(nextResult = ApiResult.NetworkError(cause))
        val result = fake.evaluate("startpos")
        assertTrue(result is ApiResult.NetworkError)
        assertEquals(cause, (result as ApiResult.NetworkError).cause)
    }

    @Test fun `FakeEngineEvalClient counts evaluate calls`() = runBlocking {
        val fake = FakeEngineEvalClient()
        fake.evaluate("startpos")
        fake.evaluate("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
        assertEquals(2, fake.callCount)
    }

    @Test fun `FakeEngineEvalClient records last evaluated fen`() = runBlocking {
        val fake = FakeEngineEvalClient()
        val fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        fake.evaluate("startpos")
        fake.evaluate(fen)
        assertEquals(fen, fake.lastFen)
    }

    @Test fun `null score propagates through fake client`() = runBlocking {
        val resp = EngineEvalResponse(score = null, bestMove = null, source = "engine")
        val fake = FakeEngineEvalClient(nextResult = ApiResult.Success(resp))
        val result = fake.evaluate("startpos")
        assertNull((result as ApiResult.Success).data.score)
    }

    @Test fun `null bestMove propagates through fake client`() = runBlocking {
        val resp = EngineEvalResponse(score = 0, bestMove = null, source = "engine")
        val fake = FakeEngineEvalClient(nextResult = ApiResult.Success(resp))
        val result = fake.evaluate("startpos")
        assertNull((result as ApiResult.Success).data.bestMove)
    }

    // ------------------------------------------------------------------
    // 21–24  HttpEngineEvalClient constructor properties
    // ------------------------------------------------------------------

    @Test fun `HttpEngineEvalClient stores baseUrl`() {
        val client = HttpEngineEvalClient(baseUrl = "http://10.0.2.2:8000")
        assertEquals("http://10.0.2.2:8000", client.baseUrl)
    }

    @Test fun `HttpEngineEvalClient has correct default timeout values`() {
        val client = HttpEngineEvalClient(baseUrl = "http://localhost")
        assertEquals(HttpEngineEvalClient.DEFAULT_CONNECT_TIMEOUT_MS, client.connectTimeoutMs)
        assertEquals(HttpEngineEvalClient.DEFAULT_READ_TIMEOUT_MS, client.readTimeoutMs)
    }

    @Test fun `HttpEngineEvalClient stores custom connectTimeoutMs`() {
        val client = HttpEngineEvalClient(baseUrl = "http://localhost", connectTimeoutMs = 3_000)
        assertEquals(3_000, client.connectTimeoutMs)
    }

    @Test fun `HttpEngineEvalClient stores custom readTimeoutMs`() {
        val client = HttpEngineEvalClient(baseUrl = "http://localhost", readTimeoutMs = 5_000)
        assertEquals(5_000, client.readTimeoutMs)
    }

    // ------------------------------------------------------------------
    // 25–27  Source enum values
    // ------------------------------------------------------------------

    private val validSources = setOf("engine", "cache", "book")

    @Test fun `engine is a valid source value`() {
        val resp = EngineEvalResponse(score = 0, bestMove = null, source = "engine")
        assertTrue(resp.source in validSources)
    }

    @Test fun `cache is a valid source value`() {
        val resp = EngineEvalResponse(score = 0, bestMove = null, source = "cache")
        assertTrue(resp.source in validSources)
    }

    @Test fun `book is a valid source value`() {
        val resp = EngineEvalResponse(score = 0, bestMove = null, source = "book")
        assertTrue(resp.source in validSources)
    }
}
