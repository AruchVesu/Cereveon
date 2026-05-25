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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpGameApiClient.getNextCurriculum] against a local server.
 *
 * Contract reference: POST /curriculum/next (docs/API_CONTRACTS.md §18).
 * Auth: Authorization: Bearer <token> required.
 *
 * Invariants pinned
 * -----------------
 *  1.  INT_CURR_METHOD             request method is POST.
 *  2.  INT_CURR_PATH               request path is /curriculum/next.
 *  3.  INT_CURR_CONTENT_TYPE       Content-Type is application/json.
 *  4.  INT_CURR_BEARER             Authorization Bearer header sent from tokenProvider.
 *  5.  INT_CURR_PLAYER_ID_BODY     player_id field serialised in request body.
 *  6.  INT_CURR_TOPIC_PARSED       topic field deserialised correctly.
 *  7.  INT_CURR_DIFFICULTY_PARSED  difficulty field deserialised as string band.
 *  8.  INT_CURR_EXERCISE_TYPE      exercise_type field deserialised (not format).
 *  9.  INT_CURR_PAYLOAD_PARSED     payload object entries deserialised.
 * 10.  INT_CURR_HTTP_401           401 → ApiResult.HttpError(401) (auth required).
 * 11.  INT_CURR_TIMEOUT            read timeout → ApiResult.Timeout.
 * 12.  INT_CURR_EMPTY_PAYLOAD      empty payload object → empty map (no crash).
 * 13.  INT_CURR_PROD_SHAPE         the EXACT shape llm/seca/curriculum/router.py
 *                                  emits (with extra recommendations + dominant_category
 *                                  keys absent from the DTO) decodes without throwing.
 *                                  Bidirectional pin against the wire-key drift the
 *                                  pre-2026-05-25 contract carried.
 */
class GameApiClientCurriculumTest {

    private lateinit var server: MockWebServer

    private val apiKey = "curriculum-test-key"

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

    private fun client(token: String? = "bearer-token-test", readTimeoutMs: Int = 15_000) =
        HttpGameApiClient(
            baseUrl = baseUrl(),
            apiKey = apiKey,
            readTimeoutMs = readTimeoutMs,
            tokenProvider = token?.let { { it } },
        )

    companion object {
        // ``difficulty`` is the band string emitted by
        // ``CurriculumPolicy.choose_difficulty()`` on the server — one of
        // ``"easy" | "medium" | "hard"``.  Earlier revisions of these
        // fixtures used a numeric literal (``0.65``) because the Android
        // DTO declared the field as ``Float`` — that mismatch threw
        // ``JsonDecodingException`` at every live call site since the
        // 2026-04 kotlinx-serialization migration.  The shape below is
        // now the real wire shape ``next_training()`` ships.
        private const val CURRICULUM_OK_BODY = """
{
  "topic": "endgame_technique",
  "difficulty": "hard",
  "exercise_type": "drill",
  "payload": {
    "position": "8/8/4k3/8/3K4/8/8/8 w - - 0 1",
    "target": "win"
  }
}"""

        private const val CURRICULUM_EMPTY_PAYLOAD = """
{
  "topic": "tactics",
  "difficulty": "easy",
  "exercise_type": "puzzle",
  "payload": {}
}"""

        /**
         * Verbatim reproduction of ``next_training()``'s response body —
         * captured by running the Python contract test against an
         * in-memory SQLite session.  Includes the ``recommendations``
         * and ``dominant_category`` keys that ``ignoreUnknownKeys = true``
         * must silently absorb without throwing.  Bidirectional pin
         * against the wire-key drift the pre-2026-05-25 contract had —
         * both sides now share one source of truth for the field types
         * and the extra fields.
         */
        private const val CURRICULUM_PROD_SHAPE = """
{
  "topic": "opening_principles",
  "difficulty": "medium",
  "exercise_type": "mixed_training",
  "payload": {
    "session_minutes": 20,
    "focus": "opening_principles",
    "difficulty": "medium",
    "exercise": "mixed_training"
  },
  "recommendations": [],
  "dominant_category": null
}"""
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 1–3  HTTP method, path, Content-Type
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        client().getNextCurriculum()
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_CURR_PATH - request path is slash curriculum slash next`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        client().getNextCurriculum()
        assertEquals("/curriculum/next", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    // INT_CURR_CONTENT_TYPE retired in PR 27 (2026-05-15).  BaseHttpClient
    // only sets Content-Type: application/json when a body is present;
    // /curriculum/next is body-less since PR 27, so the header is absent.
    // INT_CURR_EMPTY_BODY below is the inverse pin.

    // ─────────────────────────────────────────────────────────────────────────
    // 4  Auth header
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_BEARER - Authorization Bearer header sent from tokenProvider`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        client(token = "my-bearer-token").getNextCurriculum()
        val auth = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Authorization")
        assertEquals("Bearer my-bearer-token", auth)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 5  Request body (empty since PR 27 — server derives player from JWT)
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_EMPTY_BODY - request body is empty after PR-27 wire-noise removal`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        client().getNextCurriculum()
        // Pre-PR-27 Android sent {"player_id": "..."}; server silently
        // dropped it (used get_current_player from JWT instead).  The
        // wire-noise was removed in PR 27 — body must now be empty.
        val rawBody = server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8()
        assertTrue(
            "Body must be empty (was: \"$rawBody\"); /curriculum/next derives player from JWT",
            rawBody.isEmpty(),
        )
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 6–9  Response deserialisation
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_TOPIC_PARSED - topic field deserialised correctly`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        val result = client().getNextCurriculum()
        assertTrue(result is ApiResult.Success<*>)
        val rec = (result as ApiResult.Success<*>).data as CurriculumRecommendation
        assertEquals("endgame_technique", rec.topic)
    }

    @Test
    fun `INT_CURR_DIFFICULTY_PARSED - difficulty field deserialised as string band`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        val result = client().getNextCurriculum()
        assertTrue(result is ApiResult.Success<*>)
        val rec = (result as ApiResult.Success<*>).data as CurriculumRecommendation
        assertEquals("hard", rec.difficulty)
    }

    @Test
    fun `INT_CURR_EXERCISE_TYPE - exercise_type field deserialised not format`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        val result = client().getNextCurriculum()
        assertTrue(result is ApiResult.Success<*>)
        val rec = (result as ApiResult.Success<*>).data as CurriculumRecommendation
        assertEquals("drill", rec.exerciseType)
    }

    @Test
    fun `INT_CURR_PAYLOAD_PARSED - payload object entries are deserialised`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_OK_BODY))
        val result = client().getNextCurriculum()
        assertTrue(result is ApiResult.Success<*>)
        val rec = (result as ApiResult.Success<*>).data as CurriculumRecommendation
        assertNotNull("payload must be non-null", rec.payload)
        assertEquals("win", rec.payload["target"])
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 10  Auth error
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_HTTP_401 - 401 returns HttpError 401`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401)
            .setBody("""{"detail":"Unauthorized"}"""))
        val result = client().getNextCurriculum()
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 11  Timeout
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_TIMEOUT - short read timeout returns ApiResult Timeout`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBodyDelay(500, TimeUnit.MILLISECONDS)
                .setBody(CURRICULUM_OK_BODY),
        )
        val result = client(readTimeoutMs = 1).getNextCurriculum()
        assertTrue("Expected Timeout, got: $result", result is ApiResult.Timeout)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 12  Empty payload
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_EMPTY_PAYLOAD - empty payload object produces empty map without crash`() =
        runBlocking {
            server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_EMPTY_PAYLOAD))
            val result = client().getNextCurriculum()
            assertTrue(result is ApiResult.Success<*>)
            val rec = (result as ApiResult.Success<*>).data as CurriculumRecommendation
            assertTrue("payload must be empty map", rec.payload.isEmpty())
        }

    // ─────────────────────────────────────────────────────────────────────────
    // 13  Bidirectional shape pin — verbatim prod response decodes cleanly
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_CURR_PROD_SHAPE - verbatim next_training response decodes without throwing`() =
        runBlocking {
            // Wire-shape pin against the wire-key drift the pre-2026-05-25 contract
            // had — extra ``recommendations`` and ``dominant_category`` keys must be
            // absorbed by ``ignoreUnknownKeys = true``, and the ``difficulty: "medium"``
            // band string must deserialise as a string (not throw a JsonDecodingException
            // as it did before the 2026-05-25 wire-shape fix).
            server.enqueue(MockResponse().setResponseCode(200).setBody(CURRICULUM_PROD_SHAPE))
            val result = client().getNextCurriculum()
            assertTrue(
                "Verbatim Python /curriculum/next shape must parse: $result",
                result is ApiResult.Success<*>,
            )
            val rec = (result as ApiResult.Success<*>).data as CurriculumRecommendation
            assertEquals("opening_principles", rec.topic)
            assertEquals("medium", rec.difficulty)
            assertEquals("mixed_training", rec.exerciseType)
            assertEquals("medium", rec.payload["difficulty"])
            assertEquals("20", rec.payload["session_minutes"])
        }
}
