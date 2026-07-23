package com.cereveon.myapp

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpReviewApiClient] against a real local
 * HTTP server.
 *
 * Contract references: llm/seca/review/router.py + docs/API_CONTRACTS.md §39/§39a.
 *
 * Pinned invariants
 * -----------------
 * START_METHOD      POST /game/{id}/review uses HTTP POST.
 * START_PATH        request path embeds the event id.
 * START_BEARER      Authorization Bearer header is sent.
 * START_202         202 Accepted parses as Success (queued row).
 * START_200         200 OK parses as Success (existing complete row).
 * START_400_BODY    400 carries the structured eligibility body.
 * START_ROTATES     X-Auth-Token response header reaches tokenSink.
 *
 * GET_METHOD        GET /game/{id}/review uses HTTP GET.
 * GET_404           404 (no review yet) → ApiResult.HttpError(404).
 * GET_WAVES_NULL    queued row: engine/moments/llm all null.
 * GET_FULL          complete row: bands, moments, llm texts, entitlement
 *                   and isTerminal all decode.
 */
class ReviewApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        // 127.0.0.1 bind — never a public interface in CI (memory note:
        // feedback_android_host_jvm_tests).
        server = MockWebServer()
        server.start(InetAddress.getByName("127.0.0.1"), 0)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun client(tokenSink: ((String) -> Unit)? = null) = HttpReviewApiClient(
        baseUrl = "http://127.0.0.1:${server.port}",
        tokenSink = tokenSink,
    )

    private fun queuedBody(status: String = "queued") = """
        {
          "review_id": "rev-1",
          "event_id": "ev-1",
          "status": "$status",
          "analysis_version": 1,
          "review_mode": null,
          "engine": null,
          "moments": null,
          "llm": null,
          "error_message": null,
          "entitlement": {"metric": "import_analysis", "allowed": true,
                          "plan": "free", "limit": 3, "used": 1, "remaining": 2},
          "created_at": "2026-07-11T10:00:00",
          "completed_at": null
        }
    """.trimIndent()

    private fun completeBody() = """
        {
          "review_id": "rev-1",
          "event_id": "ev-1",
          "status": "complete",
          "analysis_version": 1,
          "review_mode": "standard",
          "engine": {
            "bands": ["equal", "equal", "worse", "losing"],
            "accuracy": 0.61,
            "counts": {"blunders": 1, "mistakes": 0, "inaccuracies": 2},
            "moves_analyzed": 16,
            "player_color": "white",
            "plies": 31,
            "meta": {"white": "revplayer", "black": "opponent1234",
                     "black_elo": "1234", "time_control": "600+5",
                     "opening": "Ruy Lopez: Breyer", "eco": "C95"}
          },
          "moments": [
            {"ply": 21, "move_number": 11, "san": "Nbd2",
             "moment_type": "blunder", "phase": "middlegame",
             "band_before": "equal", "band_after": "losing",
             "fen_before": "fen-b", "fen_after": "fen-a",
             "clock_remaining_s": 71}
          ],
          "llm": {
            "moments": [{"ply": 21, "text": "Coach text.", "source": "llm"}],
            "verdict": {"text": "Verdict text.", "source": "llm"},
            "outcome": "full"
          },
          "error_message": null,
          "entitlement": {"metric": "import_analysis", "allowed": true,
                          "plan": "pro", "limit": 50, "used": 4, "remaining": 46},
          "created_at": "2026-07-11T10:00:00",
          "completed_at": "2026-07-11T10:00:31"
        }
    """.trimIndent()

    // =======================================================================
    // startReview
    // =======================================================================

    @Test
    fun `START_METHOD - uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(202).setBody(queuedBody()))
        client().startReview("ev-1", "tok")
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `START_PATH - path embeds the event id`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(202).setBody(queuedBody()))
        client().startReview("ev-42", "tok")
        assertEquals("/game/ev-42/review", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `START_BEARER - Authorization header is sent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(202).setBody(queuedBody()))
        client().startReview("ev-1", "my-token")
        val header = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Authorization")
        assertEquals("Bearer my-token", header)
    }

    @Test
    fun `START_202 - accepted parses as Success with queued status`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(202).setBody(queuedBody()))
        val result = client().startReview("ev-1", "tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<GameReviewResponse>).data
        assertEquals(GameReviewResponse.STATUS_QUEUED, data.status)
        assertEquals(false, data.isTerminal)
        assertEquals(2, data.entitlement?.remaining)
    }

    @Test
    fun `START_200 - existing complete row parses as Success`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(completeBody()))
        val result = client().startReview("ev-1", "tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<GameReviewResponse>).data
        assertEquals(GameReviewResponse.STATUS_COMPLETE, data.status)
        assertTrue(data.isTerminal)
    }

    @Test
    fun `START_400_BODY - eligibility error carries the structured body`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(400)
                .setBody("""{"detail":{"code":"too_short","message":"too short"}}""")
        )
        val result = client().startReview("ev-1", "tok")
        assertTrue(result is ApiResult.HttpError)
        val err = result as ApiResult.HttpError
        assertEquals(400, err.code)
        assertNotNull(err.body)
        assertTrue(err.body!!.contains("too_short"))
    }

    @Test
    fun `START_ROTATES - X-Auth-Token reaches the sink`() = runBlocking {
        var rotated: String? = null
        server.enqueue(
            MockResponse().setResponseCode(202)
                .setHeader("X-Auth-Token", "fresh-jwt")
                .setBody(queuedBody())
        )
        client(tokenSink = { rotated = it }).startReview("ev-1", "tok")
        assertEquals("fresh-jwt", rotated)
    }

    // =======================================================================
    // getReview
    // =======================================================================

    @Test
    fun `GET_METHOD - uses HTTP GET on the same path`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(queuedBody("running")))
        client().getReview("ev-7", "tok")
        val request = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("GET", request.method)
        assertEquals("/game/ev-7/review", request.path)
    }

    @Test
    fun `GET_404 - no review yet maps to HttpError 404`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(404)
                .setBody("""{"detail":"no review for this game yet"}""")
        )
        val result = client().getReview("ev-1", "tok")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(404, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `GET_WAVES_NULL - queued row decodes with null wave payloads`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(queuedBody()))
        val result = client().getReview("ev-1", "tok")
        val data = (result as ApiResult.Success<GameReviewResponse>).data
        assertNull(data.engine)
        assertNull(data.moments)
        assertNull(data.llm)
    }

    @Test
    fun `GET_FULL - complete row decodes every section`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(completeBody()))
        val result = client().getReview("ev-1", "tok")
        val data = (result as ApiResult.Success<GameReviewResponse>).data

        assertEquals(listOf("equal", "equal", "worse", "losing"), data.engine!!.bands)
        assertEquals(1, data.engine!!.counts.blunders)
        assertEquals("Ruy Lopez: Breyer", data.engine!!.meta.opening)
        assertEquals("white", data.engine!!.playerColor)

        val moment = data.moments!!.single()
        assertEquals(21, moment.ply)
        assertEquals(ReviewMoment.TYPE_BLUNDER, moment.momentType)
        assertEquals("equal", moment.bandBefore)
        assertEquals("losing", moment.bandAfter)
        assertEquals(71, moment.clockRemainingS)

        assertEquals("Coach text.", data.llm!!.moments.single().text)
        assertEquals("Verdict text.", data.llm!!.verdict?.text)
        assertEquals(ReviewLlm.OUTCOME_FULL, data.llm!!.outcome)
        assertEquals("standard", data.reviewMode)
        assertEquals(46, data.entitlement?.remaining)
    }
}
