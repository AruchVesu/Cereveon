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
 * Unit tests for the game session API client abstraction layer.
 *
 * Covers:
 *  - [GameStartRequest] / [GameStartResponse] data classes
 *  - [GameFinishRequest] / [GameFinishResponse] data classes
 *  - [CoachActionDto] / [CoachContentDto] data classes
 *  - [GameApiClient] interface contract via [FakeGameApiClient]
 *  - [HttpGameApiClient] constructor properties
 *
 * [HttpGameApiClient] network I/O is not tested here; it is exercised in
 * integration / instrumented tests against a live or test-double server.
 *
 * Invariants pinned
 * -----------------
 *  1.  START_REQUEST_PLAYER_ID:          GameStartRequest retains playerId.
 *  2.  START_RESPONSE_GAME_ID:           GameStartResponse retains gameId.
 *  3.  START_REQUEST_EQUALITY:           Two identical GameStartRequests are equal.
 *  4.  START_RESPONSE_EQUALITY:          Two identical GameStartResponses are equal.
 *  5.  START_REQUEST_INEQUALITY:         GameStartRequests differ when playerId differs.
 *  6.  FINISH_REQUEST_FIELDS:            GameFinishRequest retains all fields.
 *  7.  FINISH_REQUEST_RESULT_VALUES:     result field accepts win/loss/draw.
 *  8.  FINISH_REQUEST_NULL_PLAYER:       GameFinishRequest.playerId defaults to null.
 *  9.  FINISH_REQUEST_EMPTY_WEAKNESSES:  GameFinishRequest.weaknesses defaults to empty.
 * 10.  FINISH_REQUEST_EQUALITY:          Two identical GameFinishRequests are equal.
 * 11.  FINISH_REQUEST_INEQUALITY:        GameFinishRequests differ when result differs.
 * 12.  COACH_ACTION_FIELDS:             CoachActionDto retains type, weakness, reason.
 * 13.  COACH_ACTION_NULL_WEAKNESS:      CoachActionDto accepts null weakness.
 * 14.  COACH_ACTION_NULL_REASON:        CoachActionDto accepts null reason.
 * 15.  COACH_CONTENT_FIELDS:            CoachContentDto retains title and description.
 * 16.  COACH_CONTENT_EMPTY_PAYLOAD:     CoachContentDto payload defaults to empty map.
 * 17.  FINISH_RESPONSE_FIELDS:          GameFinishResponse retains all top-level fields.
 * 18.  FAKE_START_RETURNS_SUCCESS:      FakeGameApiClient returns configured startGame result.
 * 19.  FAKE_FINISH_RETURNS_SUCCESS:     FakeGameApiClient returns configured finishGame result.
 * 20.  FAKE_START_HTTP_ERROR:           FakeGameApiClient returns HttpError for startGame.
 * 21.  FAKE_FINISH_HTTP_ERROR:          FakeGameApiClient returns HttpError for finishGame.
 * 22.  FAKE_TIMEOUT:                    FakeGameApiClient returns Timeout for finishGame.
 * 23.  FAKE_CALL_COUNT:                 FakeGameApiClient counts calls correctly.
 * 24.  FAKE_LAST_PLAYER_ID:             FakeGameApiClient records last startGame playerId.
 * 25.  FAKE_LAST_FINISH_REQUEST:        FakeGameApiClient records last finishGame request.
 * 26.  HTTP_CLIENT_DEFAULTS:            HttpGameApiClient has correct default timeouts.
 * 27.  HTTP_CLIENT_BASE_URL:            HttpGameApiClient stores baseUrl.
 * 28.  HTTP_CLIENT_API_KEY:             HttpGameApiClient stores apiKey.
 * 29.  HTTP_CLIENT_TOKEN_NULL_DEFAULT:  HttpGameApiClient.tokenProvider defaults to null.
 * 30.  HTTP_CLIENT_TOKEN_STORED:        HttpGameApiClient stores a supplied tokenProvider.
 * 31.  TRAINING_MODEL_FIELDS:           TrainingRecommendation retains all four fields.
 * 32.  TRAINING_MODEL_EQUALITY:         Two identical TrainingRecommendations are equal.
 * 33.  TRAINING_MODEL_INEQUALITY:       TrainingRecommendations differ when topic differs.
 * 34.  FAKE_TRAINING_SUCCESS:           FakeGameApiClient returns configured getNextTraining result.
 * 35.  FAKE_TRAINING_HTTP_ERROR:        FakeGameApiClient returns HttpError for getNextTraining.
 * 36.  FAKE_TRAINING_TIMEOUT:           FakeGameApiClient returns Timeout for getNextTraining.
 * 37.  FAKE_TRAINING_CALL_COUNT:        FakeGameApiClient counts getNextTraining calls.
 * 38.  FAKE_TRAINING_LAST_PLAYER_ID:    FakeGameApiClient records last getNextTraining playerId.
 * 39.  TRAINING_RESPONSE_TOPIC_STRING:  topic field is a non-empty string in success response.
 * 40.  TRAINING_RESPONSE_FORMAT_STRING: format field is a non-empty string in success response.
 * 41.  TRAINING_RESPONSE_DIFFICULTY_RANGE: difficulty is in 0.0..1.0.
 * 42.  TRAINING_RESPONSE_EXPECTED_GAIN_NON_NEGATIVE: expectedGain is >= 0.
 */
class GameApiClientTest {

    // ------------------------------------------------------------------
    // Test double
    // ------------------------------------------------------------------

    private class FakeGameApiClient(
        var nextStartResult: ApiResult<GameStartResponse> =
            ApiResult.Success(GameStartResponse("game-42")),
        var nextFinishResult: ApiResult<GameFinishResponse> =
            ApiResult.Success(
                GameFinishResponse(
                    status = "stored",
                    newRating = 1200f,
                    confidence = 0.8f,
                    coachAction = CoachActionDto("REFLECT", null, null),
                    coachContent = CoachContentDto("Post-game reflection", "Review your moves."),
                )
            ),
        var nextTrainingResult: ApiResult<TrainingRecommendation> =
            ApiResult.Success(
                TrainingRecommendation(
                    topic = "tactics",
                    difficulty = 0.6f,
                    format = "puzzle",
                    expectedGain = 2.5f,
                )
            ),
    ) : GameApiClient {
        var startCallCount = 0
        var finishCallCount = 0
        var trainingCallCount = 0
        var lastPlayerId: String? = null
        var lastFinishRequest: GameFinishRequest? = null
        var lastTrainingPlayerId: String? = null

        override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> {
            startCallCount++
            lastPlayerId = playerId
            return nextStartResult
        }

        override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> {
            finishCallCount++
            lastFinishRequest = req
            return nextFinishResult
        }

        override suspend fun getNextTraining(playerId: String): ApiResult<TrainingRecommendation> {
            trainingCallCount++
            lastTrainingPlayerId = playerId
            return nextTrainingResult
        }
    }

    // ------------------------------------------------------------------
    // 1–5  GameStartRequest / GameStartResponse
    // ------------------------------------------------------------------

    @Test
    fun `GameStartRequest retains playerId`() {
        val req = GameStartRequest(playerId = "player-99")
        assertEquals("player-99", req.playerId)
    }

    @Test
    fun `GameStartResponse retains gameId`() {
        val resp = GameStartResponse(gameId = "g-123")
        assertEquals("g-123", resp.gameId)
    }

    @Test
    fun `two identical GameStartRequests are equal`() {
        val a = GameStartRequest("uid-1")
        val b = GameStartRequest("uid-1")
        assertEquals(a, b)
    }

    @Test
    fun `two identical GameStartResponses are equal`() {
        val a = GameStartResponse("g-1")
        val b = GameStartResponse("g-1")
        assertEquals(a, b)
    }

    @Test
    fun `GameStartRequests differ when playerId differs`() {
        val a = GameStartRequest("uid-A")
        val b = GameStartRequest("uid-B")
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 6–11  GameFinishRequest
    // ------------------------------------------------------------------

    @Test
    fun `GameFinishRequest retains all fields`() {
        val req =
            GameFinishRequest(
                pgn = "1. e2e4 e7e5",
                result = "win",
                accuracy = 0.85f,
                weaknesses = mapOf("tactics" to 0.3f),
                playerId = "uid-1",
            )
        assertEquals("1. e2e4 e7e5", req.pgn)
        assertEquals("win", req.result)
        assertEquals(0.85f, req.accuracy)
        assertEquals(mapOf("tactics" to 0.3f), req.weaknesses)
        assertEquals("uid-1", req.playerId)
    }

    @Test
    fun `GameFinishRequest result accepts win loss and draw`() {
        listOf("win", "loss", "draw").forEach { result ->
            val req = GameFinishRequest(pgn = "1. e2e4", result = result, accuracy = 0.5f)
            assertEquals(result, req.result)
        }
    }

    @Test
    fun `GameFinishRequest playerId defaults to null`() {
        val req = GameFinishRequest(pgn = "1. e2e4", result = "draw", accuracy = 0.5f)
        assertNull(req.playerId)
    }

    @Test
    fun `GameFinishRequest weaknesses defaults to empty map`() {
        val req = GameFinishRequest(pgn = "1. e2e4", result = "draw", accuracy = 0.5f)
        assertTrue(req.weaknesses.isEmpty())
    }

    @Test
    fun `two identical GameFinishRequests are equal`() {
        val a = GameFinishRequest("1. e2e4", "win", 0.9f)
        val b = GameFinishRequest("1. e2e4", "win", 0.9f)
        assertEquals(a, b)
    }

    @Test
    fun `GameFinishRequests differ when result differs`() {
        val a = GameFinishRequest("1. e2e4", "win", 0.9f)
        val b = GameFinishRequest("1. e2e4", "loss", 0.9f)
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 12–16  CoachActionDto / CoachContentDto
    // ------------------------------------------------------------------

    @Test
    fun `CoachActionDto retains type weakness and reason`() {
        val dto = CoachActionDto(type = "DRILL", weakness = "tactics", reason = "Blunder rate high")
        assertEquals("DRILL", dto.type)
        assertEquals("tactics", dto.weakness)
        assertEquals("Blunder rate high", dto.reason)
    }

    @Test
    fun `CoachActionDto accepts null weakness`() {
        val dto = CoachActionDto(type = "REFLECT", weakness = null, reason = "Good game")
        assertNull(dto.weakness)
    }

    @Test
    fun `CoachActionDto accepts null reason`() {
        val dto = CoachActionDto(type = "NONE", weakness = null, reason = null)
        assertNull(dto.reason)
    }

    @Test
    fun `CoachContentDto retains title and description`() {
        val dto = CoachContentDto(title = "Post-game", description = "Review your mistakes.")
        assertEquals("Post-game", dto.title)
        assertEquals("Review your mistakes.", dto.description)
    }

    @Test
    fun `CoachContentDto payload defaults to empty map`() {
        val dto = CoachContentDto(title = "Keep playing", description = "No action needed.")
        assertTrue(dto.payload.isEmpty())
    }

    // ------------------------------------------------------------------
    // 17  GameFinishResponse
    // ------------------------------------------------------------------

    @Test
    fun `GameFinishResponse retains all top-level fields`() {
        val resp =
            GameFinishResponse(
                status = "stored",
                newRating = 1350f,
                confidence = 0.75f,
                coachAction = CoachActionDto("PUZZLE", "tactics", null),
                coachContent = CoachContentDto("Puzzle challenge", "Sharpen calculation."),
            )
        assertEquals("stored", resp.status)
        assertEquals(1350f, resp.newRating)
        assertEquals(0.75f, resp.confidence)
        assertEquals("PUZZLE", resp.coachAction.type)
        assertEquals("Puzzle challenge", resp.coachContent.title)
    }

    // ------------------------------------------------------------------
    // 18–22  FakeGameApiClient — controlled result variants
    // ------------------------------------------------------------------

    @Test
    fun `FakeGameApiClient returns configured startGame success`() =
        runBlocking {
            val fake = FakeGameApiClient(nextStartResult = ApiResult.Success(GameStartResponse("g-99")))
            val result = fake.startGame("player-1")
            assertTrue(result is ApiResult.Success)
            assertEquals("g-99", (result as ApiResult.Success).data.gameId)
        }

    @Test
    fun `FakeGameApiClient returns configured finishGame success`() =
        runBlocking {
            val resp =
                GameFinishResponse(
                    status = "stored",
                    newRating = 1400f,
                    confidence = 0.9f,
                    coachAction = CoachActionDto("DRILL", "endgame", null),
                    coachContent = CoachContentDto("Endgame drill", "Practice endgames."),
                )
            val fake = FakeGameApiClient(nextFinishResult = ApiResult.Success(resp))
            val result = fake.finishGame(GameFinishRequest("1. e2e4", "win", 0.8f))
            assertTrue(result is ApiResult.Success)
            assertEquals(1400f, (result as ApiResult.Success).data.newRating)
        }

    @Test
    fun `FakeGameApiClient returns HttpError for startGame`() =
        runBlocking {
            val fake = FakeGameApiClient(nextStartResult = ApiResult.HttpError(401))
            val result = fake.startGame("player-1")
            assertTrue(result is ApiResult.HttpError)
            assertEquals(401, (result as ApiResult.HttpError).code)
        }

    @Test
    fun `FakeGameApiClient returns HttpError for finishGame`() =
        runBlocking {
            val fake = FakeGameApiClient(nextFinishResult = ApiResult.HttpError(403))
            val result = fake.finishGame(GameFinishRequest("1. e2e4", "loss", 0.3f))
            assertTrue(result is ApiResult.HttpError)
            assertEquals(403, (result as ApiResult.HttpError).code)
        }

    @Test
    fun `FakeGameApiClient returns Timeout for finishGame`() =
        runBlocking {
            val fake = FakeGameApiClient(nextFinishResult = ApiResult.Timeout)
            val result = fake.finishGame(GameFinishRequest("1. e2e4", "draw", 0.5f))
            assertSame(ApiResult.Timeout, result)
        }

    // ------------------------------------------------------------------
    // 23–25  FakeGameApiClient introspection
    // ------------------------------------------------------------------

    @Test
    fun `FakeGameApiClient counts start and finish calls independently`() =
        runBlocking {
            val fake = FakeGameApiClient()
            fake.startGame("p1")
            fake.startGame("p2")
            fake.finishGame(GameFinishRequest("pgn", "win", 0.8f))
            assertEquals(2, fake.startCallCount)
            assertEquals(1, fake.finishCallCount)
        }

    @Test
    fun `FakeGameApiClient records last startGame playerId`() =
        runBlocking {
            val fake = FakeGameApiClient()
            fake.startGame("first-player")
            fake.startGame("second-player")
            assertEquals("second-player", fake.lastPlayerId)
        }

    @Test
    fun `FakeGameApiClient records last finishGame request`() =
        runBlocking {
            val fake = FakeGameApiClient()
            val req = GameFinishRequest("1. e2e4 e7e5", "draw", 0.7f, mapOf("tactics" to 0.4f), "uid-1")
            fake.finishGame(req)
            assertEquals(req, fake.lastFinishRequest)
        }

    // ------------------------------------------------------------------
    // 26–30  HttpGameApiClient constructor properties
    // ------------------------------------------------------------------

    @Test
    fun `HttpGameApiClient has correct default timeout values`() {
        val client = HttpGameApiClient(baseUrl = "http://localhost", apiKey = "key")
        assertEquals(8_000, client.connectTimeoutMs)
        assertEquals(30_000, client.readTimeoutMs)
    }

    @Test
    fun `HttpGameApiClient stores baseUrl`() {
        val client = HttpGameApiClient(baseUrl = "http://10.0.2.2:8000", apiKey = "key")
        assertEquals("http://10.0.2.2:8000", client.baseUrl)
    }

    @Test
    fun `HttpGameApiClient stores apiKey`() {
        val client = HttpGameApiClient(baseUrl = "http://localhost", apiKey = "my-secret")
        assertEquals("my-secret", client.apiKey)
    }

    @Test
    fun `HttpGameApiClient tokenProvider defaults to null`() {
        val client = HttpGameApiClient(baseUrl = "http://localhost", apiKey = "key")
        assertNull(client.tokenProvider)
    }

    @Test
    fun `HttpGameApiClient stores a supplied tokenProvider`() {
        val provider: () -> String? = { "bearer-token" }
        val client =
            HttpGameApiClient(
                baseUrl = "http://localhost",
                apiKey = "key",
                tokenProvider = provider,
            )
        assertNotNull(client.tokenProvider)
        assertEquals("bearer-token", client.tokenProvider?.invoke())
    }

    // ------------------------------------------------------------------
    // 31–33  TrainingRecommendation model
    // ------------------------------------------------------------------

    @Test
    fun `TrainingRecommendation retains all four fields`() {
        val rec = TrainingRecommendation(
            topic = "tactics",
            difficulty = 0.7f,
            format = "puzzle",
            expectedGain = 3.2f,
        )
        assertEquals("tactics", rec.topic)
        assertEquals(0.7f, rec.difficulty)
        assertEquals("puzzle", rec.format)
        assertEquals(3.2f, rec.expectedGain)
    }

    @Test
    fun `two identical TrainingRecommendations are equal`() {
        val a = TrainingRecommendation("endgame", 0.5f, "drill", 1.0f)
        val b = TrainingRecommendation("endgame", 0.5f, "drill", 1.0f)
        assertEquals(a, b)
    }

    @Test
    fun `TrainingRecommendations differ when topic differs`() {
        val a = TrainingRecommendation("tactics", 0.5f, "puzzle", 1.0f)
        val b = TrainingRecommendation("endgame", 0.5f, "puzzle", 1.0f)
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 34–36  FakeGameApiClient — getNextTraining result variants
    // ------------------------------------------------------------------

    @Test
    fun `FakeGameApiClient returns configured getNextTraining success`() =
        runBlocking {
            val rec = TrainingRecommendation("tactics", 0.6f, "puzzle", 2.5f)
            val fake = FakeGameApiClient(nextTrainingResult = ApiResult.Success(rec))
            val result = fake.getNextTraining("player-1")
            assertTrue(result is ApiResult.Success)
            assertEquals("tactics", (result as ApiResult.Success).data.topic)
            assertEquals("puzzle", result.data.format)
        }

    @Test
    fun `FakeGameApiClient returns HttpError for getNextTraining`() =
        runBlocking {
            val fake = FakeGameApiClient(nextTrainingResult = ApiResult.HttpError(401))
            val result = fake.getNextTraining("player-1")
            assertTrue(result is ApiResult.HttpError)
            assertEquals(401, (result as ApiResult.HttpError).code)
        }

    @Test
    fun `FakeGameApiClient returns Timeout for getNextTraining`() =
        runBlocking {
            val fake = FakeGameApiClient(nextTrainingResult = ApiResult.Timeout)
            val result = fake.getNextTraining("player-1")
            assertSame(ApiResult.Timeout, result)
        }

    // ------------------------------------------------------------------
    // 37–38  FakeGameApiClient introspection — getNextTraining
    // ------------------------------------------------------------------

    @Test
    fun `FakeGameApiClient counts getNextTraining calls independently`() =
        runBlocking {
            val fake = FakeGameApiClient()
            fake.getNextTraining("p1")
            fake.getNextTraining("p2")
            fake.startGame("p3")
            assertEquals(2, fake.trainingCallCount)
            assertEquals(1, fake.startCallCount)
        }

    @Test
    fun `FakeGameApiClient records last getNextTraining playerId`() =
        runBlocking {
            val fake = FakeGameApiClient()
            fake.getNextTraining("first-player")
            fake.getNextTraining("second-player")
            assertEquals("second-player", fake.lastTrainingPlayerId)
        }

    // ------------------------------------------------------------------
    // 39–42  TrainingRecommendation field contracts
    // ------------------------------------------------------------------

    @Test
    fun `TrainingRecommendation topic field is a non-empty string`() {
        val rec = TrainingRecommendation(topic = "tactics", difficulty = 0.5f, format = "puzzle", expectedGain = 1.0f)
        assertTrue("topic must be non-empty", rec.topic.isNotEmpty())
    }

    @Test
    fun `TrainingRecommendation format field is a non-empty string`() {
        val rec = TrainingRecommendation(topic = "endgame", difficulty = 0.4f, format = "drill", expectedGain = 0.5f)
        assertTrue("format must be non-empty", rec.format.isNotEmpty())
    }

    @Test
    fun `TrainingRecommendation difficulty is in 0 to 1 range`() {
        val rec = TrainingRecommendation(topic = "strategy", difficulty = 0.3f, format = "game", expectedGain = 0.8f)
        assertTrue("difficulty must be >= 0", rec.difficulty >= 0f)
        assertTrue("difficulty must be <= 1", rec.difficulty <= 1f)
    }

    @Test
    fun `TrainingRecommendation expectedGain is non-negative`() {
        val rec = TrainingRecommendation(topic = "tactics", difficulty = 0.6f, format = "puzzle", expectedGain = 2.5f)
        assertTrue("expectedGain must be >= 0", rec.expectedGain >= 0f)
    }
}
