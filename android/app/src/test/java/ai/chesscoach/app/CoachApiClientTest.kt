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
 * Unit tests for the coach API client abstraction layer.
 *
 * Covers:
 *  - [ApiResult] sealed class hierarchy — all four variants
 *  - [CoachApiModels] data classes — construction, equality, nullability
 *  - [CoachApiClient] interface contract via [FakeCoachApiClient]
 *  - [PlayerProfileDto] data class — construction, equality
 *
 * [HttpCoachApiClient] network I/O is not tested here; it is exercised in
 * integration / instrumented tests against a live or test-double server.
 *
 * Invariants pinned
 * -----------------
 *  1.  RESULT_SUCCESS_DATA:                    ApiResult.Success wraps data correctly.
 *  2.  RESULT_HTTP_CODE:                       ApiResult.HttpError stores HTTP status code.
 *  3.  RESULT_NETWORK_CAUSE:                   ApiResult.NetworkError stores the exception.
 *  4.  RESULT_TIMEOUT_SINGLETON:               ApiResult.Timeout is a singleton object.
 *  5.  RESULT_SUCCESS_INEQUALITY:              Two ApiResult.Success with different data are not equal.
 *  6.  MSG_DTO_FIELDS:                         ChatMessageDto retains role and content.
 *  7.  MSG_DTO_EQUALITY:                       Two identical ChatMessageDtos are equal.
 *  8.  MSG_DTO_INEQUALITY_ROLE:                ChatMessageDtos differ when role differs.
 *  9.  MSG_DTO_INEQUALITY_CONTENT:             ChatMessageDtos differ when content differs.
 * 10.  REQUEST_BODY_FIELDS:                    ChatRequestBody retains fen and messages.
 * 11.  RESPONSE_BODY_REPLY:                    ChatResponseBody retains reply and engine signal.
 * 12.  RESPONSE_BODY_NULL_SIGNAL:              ChatResponseBody with null engineSignal is accepted.
 * 13.  EVAL_DTO_FIELDS:                        EvaluationDto retains band and side.
 * 14.  SIGNAL_DTO_FIELDS:                      EngineSignalDto retains evaluation and phase.
 * 15.  SIGNAL_DTO_NULL_EVAL:                   EngineSignalDto with null evaluation is accepted.
 * 16.  FAKE_SUCCESS_RETURN:                    FakeCoachApiClient returns configured success.
 * 17.  FAKE_HTTP_ERROR_RETURN:                 FakeCoachApiClient returns HttpError with correct code.
 * 18.  FAKE_NETWORK_ERROR_RETURN:              FakeCoachApiClient returns NetworkError with correct cause.
 * 19.  FAKE_TIMEOUT_RETURN:                    FakeCoachApiClient returns Timeout.
 * 20.  CONTRACT_REPLY_ON_SUCCESS:              Calling chat() on Success yields the reply.
 * 21.  CONTRACT_EMPTY_ON_HTTP_ERROR:           Calling chat() on HttpError yields empty string.
 * 22.  CONTRACT_EMPTY_ON_TIMEOUT:              Calling chat() on Timeout yields empty string.
 * 23.  MSG_LIST_ORDER:                         Messages in list retain insertion order.
 * 24.  RESULT_PATTERN_MATCH:                   when() correctly matches all ApiResult variants.
 * 25.  FAKE_CALL_COUNT:                        FakeCoachApiClient counts calls correctly.
 * 26.  FAKE_LAST_FEN:                          FakeCoachApiClient records the last FEN received.
 * 27.  FAKE_LAST_MESSAGES:                     FakeCoachApiClient records the last message list.
 * 28.  TOKEN_PROVIDER_NULL_DEFAULT:            HttpCoachApiClient.tokenProvider defaults to null.
 * 29.  TOKEN_PROVIDER_STORED:                  HttpCoachApiClient stores a supplied tokenProvider.
 * 30.  TOKEN_PROVIDER_RETURNS_VALUE:           The stored tokenProvider lambda is callable.
 * 31.  PLAYER_PROFILE_RETAINS_RATING:          PlayerProfileDto retains the rating field.
 * 32.  PLAYER_PROFILE_RETAINS_CONFIDENCE:      PlayerProfileDto retains the confidence field.
 * 33.  PLAYER_PROFILE_EQUALITY:               Two identical PlayerProfileDtos are equal.
 * 34.  PLAYER_PROFILE_INEQUALITY_RATING:      PlayerProfileDtos differ when rating differs.
 * 35.  PLAYER_PROFILE_INEQUALITY_CONFIDENCE:  PlayerProfileDtos differ when confidence differs.
 * 36.  REQUEST_BODY_WITH_PLAYER_PROFILE:       ChatRequestBody retains non-null playerProfile.
 * 37.  REQUEST_BODY_NULL_PLAYER_PROFILE:       ChatRequestBody accepts null playerProfile (default).
 * 38.  REQUEST_BODY_WITH_PAST_MISTAKES:        ChatRequestBody retains non-null pastMistakes list.
 * 39.  REQUEST_BODY_NULL_PAST_MISTAKES:        ChatRequestBody accepts null pastMistakes (default).
 * 40.  FAKE_RECORDS_PLAYER_PROFILE:           FakeCoachApiClient records the playerProfile passed.
 * 41.  FAKE_RECORDS_PAST_MISTAKES:            FakeCoachApiClient records the pastMistakes passed.
 * 42.  FAKE_NULL_PLAYER_PROFILE_ACCEPTED:     chat() with null playerProfile completes without error.
 * 43.  FAKE_EMPTY_PAST_MISTAKES_ACCEPTED:     chat() with empty pastMistakes list is accepted.
 */
class CoachApiClientTest {

    // ------------------------------------------------------------------
    // Test double
    // ------------------------------------------------------------------

    /**
     * Fake [CoachApiClient] for unit testing callers of the interface.
     *
     * [nextResult] is returned by every [chat] call.
     * Call introspection fields ([callCount], [lastFen], [lastMessages],
     * [lastPlayerProfile], [lastPastMistakes]) allow assertions on how the
     * client was invoked.
     */
    private class FakeCoachApiClient(
        var nextResult: ApiResult<ChatResponseBody> =
            ApiResult.Success(ChatResponseBody(reply = "Develop your pieces.", engineSignal = null)),
    ) : CoachApiClient {
        var callCount = 0
        var lastFen: String? = null
        var lastMessages: List<ChatMessageDto>? = null
        var lastPlayerProfile: PlayerProfileDto? = null
        var lastPastMistakes: List<String>? = null

        override suspend fun chat(
            fen: String,
            messages: List<ChatMessageDto>,
            playerProfile: PlayerProfileDto?,
            pastMistakes: List<String>?,
            moveCount: Int?,
            coachVoice: String?,
        ): ApiResult<ChatResponseBody> {
            callCount++
            lastFen = fen
            lastMessages = messages
            lastPlayerProfile = playerProfile
            lastPastMistakes = pastMistakes
            return nextResult
        }
    }

    // ------------------------------------------------------------------
    // 1–5  ApiResult sealed hierarchy
    // ------------------------------------------------------------------

    @Test
    fun `ApiResult Success wraps data correctly`() {
        val response = ChatResponseBody(reply = "Castle kingside.", engineSignal = null)
        val result: ApiResult<ChatResponseBody> = ApiResult.Success(response)
        assertTrue(result is ApiResult.Success)
        assertEquals("Castle kingside.", (result as ApiResult.Success).data.reply)
    }

    @Test
    fun `ApiResult HttpError stores status code`() {
        val result: ApiResult<ChatResponseBody> = ApiResult.HttpError(503)
        assertTrue(result is ApiResult.HttpError)
        assertEquals(503, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `ApiResult NetworkError stores the exception`() {
        val cause = RuntimeException("No route to host")
        val result: ApiResult<ChatResponseBody> = ApiResult.NetworkError(cause)
        assertTrue(result is ApiResult.NetworkError)
        assertSame(cause, (result as ApiResult.NetworkError).cause)
    }

    @Test
    fun `ApiResult Timeout is a singleton object`() {
        val t1: ApiResult<ChatResponseBody> = ApiResult.Timeout
        val t2: ApiResult<ChatResponseBody> = ApiResult.Timeout
        assertSame(t1, t2)
    }

    @Test
    fun `two ApiResult Success with different data are not equal`() {
        val r1 = ApiResult.Success(ChatResponseBody("Move one.", null))
        val r2 = ApiResult.Success(ChatResponseBody("Move two.", null))
        assertNotEquals(r1, r2)
    }

    // ------------------------------------------------------------------
    // 6–9  ChatMessageDto
    // ------------------------------------------------------------------

    @Test
    fun `ChatMessageDto retains role and content`() {
        val dto = ChatMessageDto(role = "user", content = "What should I do?")
        assertEquals("user", dto.role)
        assertEquals("What should I do?", dto.content)
    }

    @Test
    fun `two identical ChatMessageDtos are equal`() {
        val a = ChatMessageDto("assistant", "Knight to f3.")
        val b = ChatMessageDto("assistant", "Knight to f3.")
        assertEquals(a, b)
    }

    @Test
    fun `ChatMessageDtos differ when role differs`() {
        val a = ChatMessageDto("user", "Same text.")
        val b = ChatMessageDto("assistant", "Same text.")
        assertNotEquals(a, b)
    }

    @Test
    fun `ChatMessageDtos differ when content differs`() {
        val a = ChatMessageDto("user", "Text A")
        val b = ChatMessageDto("user", "Text B")
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 10  ChatRequestBody
    // ------------------------------------------------------------------

    @Test
    fun `ChatRequestBody retains fen and messages list`() {
        val msgs =
            listOf(
                ChatMessageDto("user", "Hello"),
                ChatMessageDto("assistant", "Hi"),
            )
        val body =
            ChatRequestBody(
                fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                messages = msgs,
            )
        assertEquals("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", body.fen)
        assertEquals(2, body.messages.size)
        assertEquals("Hello", body.messages[0].content)
        assertEquals("Hi", body.messages[1].content)
    }

    // ------------------------------------------------------------------
    // 11–12  ChatResponseBody
    // ------------------------------------------------------------------

    @Test
    fun `ChatResponseBody retains reply and engine signal`() {
        val signal =
            EngineSignalDto(
                evaluation = EvaluationDto(band = "slight_advantage", side = "white"),
                phase = "middlegame",
            )
        val body = ChatResponseBody(reply = "Centralise your rooks.", engineSignal = signal)
        assertEquals("Centralise your rooks.", body.reply)
        assertNotNull(body.engineSignal)
        assertEquals("middlegame", body.engineSignal!!.phase)
        assertEquals("slight_advantage", body.engineSignal!!.evaluation?.band)
    }

    @Test
    fun `ChatResponseBody with null engineSignal is accepted`() {
        val body = ChatResponseBody(reply = "Good move.", engineSignal = null)
        assertEquals("Good move.", body.reply)
        assertNull(body.engineSignal)
    }

    // ------------------------------------------------------------------
    // 13–15  EvaluationDto / EngineSignalDto
    // ------------------------------------------------------------------

    @Test
    fun `EvaluationDto retains band and side`() {
        val dto = EvaluationDto(band = "equal", side = "black")
        assertEquals("equal", dto.band)
        assertEquals("black", dto.side)
    }

    @Test
    fun `EngineSignalDto retains evaluation and phase`() {
        val eval = EvaluationDto(band = "decisive_advantage", side = "white")
        val sig = EngineSignalDto(evaluation = eval, phase = "endgame")
        assertEquals(eval, sig.evaluation)
        assertEquals("endgame", sig.phase)
    }

    @Test
    fun `EngineSignalDto with null evaluation is accepted`() {
        val sig = EngineSignalDto(evaluation = null, phase = "opening")
        assertNull(sig.evaluation)
        assertEquals("opening", sig.phase)
    }

    // ------------------------------------------------------------------
    // 16–19  FakeCoachApiClient — controlled result variants
    // ------------------------------------------------------------------

    @Test
    fun `FakeCoachApiClient returns configured success response`() =
        runBlocking {
            val fake =
                FakeCoachApiClient(
                    nextResult = ApiResult.Success(ChatResponseBody("Control the centre.", null)),
                )
            val result = fake.chat("rnbqkbnr/8/8/8/8/8/8/RNBQKBNR w KQkq - 0 1", emptyList())
            assertTrue(result is ApiResult.Success)
            assertEquals("Control the centre.", (result as ApiResult.Success).data.reply)
        }

    @Test
    fun `FakeCoachApiClient returns HttpError with correct code`() =
        runBlocking {
            val fake = FakeCoachApiClient(nextResult = ApiResult.HttpError(401))
            val result = fake.chat("fen", emptyList())
            assertTrue(result is ApiResult.HttpError)
            assertEquals(401, (result as ApiResult.HttpError).code)
        }

    @Test
    fun `FakeCoachApiClient returns NetworkError with correct cause`() =
        runBlocking {
            val cause = RuntimeException("Connection refused")
            val fake = FakeCoachApiClient(nextResult = ApiResult.NetworkError(cause))
            val result = fake.chat("fen", emptyList())
            assertTrue(result is ApiResult.NetworkError)
            assertSame(cause, (result as ApiResult.NetworkError).cause)
        }

    @Test
    fun `FakeCoachApiClient returns Timeout`() =
        runBlocking {
            val fake = FakeCoachApiClient(nextResult = ApiResult.Timeout)
            val result = fake.chat("fen", emptyList())
            assertSame(ApiResult.Timeout, result)
        }

    // ------------------------------------------------------------------
    // 20–22  Interface contract — caller when-branch behaviour
    // ------------------------------------------------------------------

    @Test
    fun `calling chat on Success yields the reply`() =
        runBlocking {
            val expectedReply = "Develop your knights before bishops."
            val fake =
                FakeCoachApiClient(
                    nextResult = ApiResult.Success(ChatResponseBody(expectedReply, null)),
                )
            val result = fake.chat("startpos", listOf(ChatMessageDto("user", "Hint?")))
            val reply =
                when (result) {
                    is ApiResult.Success -> result.data.reply
                    is ApiResult.HttpError, is ApiResult.NetworkError, ApiResult.Timeout -> ""
                }
            assertEquals(expectedReply, reply)
        }

    @Test
    fun `HttpError result produces empty string via when branch`() =
        runBlocking {
            val fake = FakeCoachApiClient(nextResult = ApiResult.HttpError(500))
            val result = fake.chat("fen", emptyList())
            val reply =
                when (result) {
                    is ApiResult.Success -> result.data.reply
                    is ApiResult.HttpError, is ApiResult.NetworkError, ApiResult.Timeout -> ""
                }
            assertEquals("", reply)
        }

    @Test
    fun `Timeout result produces empty string via when branch`() =
        runBlocking {
            val fake = FakeCoachApiClient(nextResult = ApiResult.Timeout)
            val result = fake.chat("fen", emptyList())
            val reply =
                when (result) {
                    is ApiResult.Success -> result.data.reply
                    is ApiResult.HttpError, is ApiResult.NetworkError, ApiResult.Timeout -> ""
                }
            assertEquals("", reply)
        }

    // ------------------------------------------------------------------
    // 23  Message list ordering
    // ------------------------------------------------------------------

    @Test
    fun `ChatMessageDto list retains insertion order`() {
        val list =
            listOf(
                ChatMessageDto("user", "First"),
                ChatMessageDto("assistant", "Second"),
                ChatMessageDto("user", "Third"),
            )
        assertEquals("First", list[0].content)
        assertEquals("Second", list[1].content)
        assertEquals("Third", list[2].content)
    }

    // ------------------------------------------------------------------
    // 24  Pattern matching across all ApiResult variants
    // ------------------------------------------------------------------

    @Test
    fun `when expression matches all four ApiResult variants correctly`() {
        val results: List<ApiResult<ChatResponseBody>> =
            listOf(
                ApiResult.Success(ChatResponseBody("reply", null)),
                ApiResult.HttpError(404),
                ApiResult.NetworkError(RuntimeException("err")),
                ApiResult.Timeout,
            )
        val kinds =
            results.map { result ->
                when (result) {
                    is ApiResult.Success -> "success"
                    is ApiResult.HttpError -> "http"
                    is ApiResult.NetworkError -> "network"
                    ApiResult.Timeout -> "timeout"
                }
            }
        assertEquals(listOf("success", "http", "network", "timeout"), kinds)
    }

    // ------------------------------------------------------------------
    // 25–27  FakeCoachApiClient introspection
    // ------------------------------------------------------------------

    @Test
    fun `FakeCoachApiClient records call count across multiple calls`() =
        runBlocking {
            val fake = FakeCoachApiClient()
            fake.chat("fen1", emptyList())
            fake.chat("fen2", emptyList())
            assertEquals(2, fake.callCount)
        }

    @Test
    fun `FakeCoachApiClient records the last FEN received`() =
        runBlocking {
            val fake = FakeCoachApiClient()
            val fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
            fake.chat(fen, emptyList())
            assertEquals(fen, fake.lastFen)
        }

    @Test
    fun `FakeCoachApiClient records the last message list received`() =
        runBlocking {
            val fake = FakeCoachApiClient()
            val msgs =
                listOf(
                    ChatMessageDto("user", "What's the plan?"),
                    ChatMessageDto("assistant", "Attack the king."),
                )
            fake.chat("fen", msgs)
            assertEquals(msgs, fake.lastMessages)
        }

    // ------------------------------------------------------------------
    // 28–30  HttpCoachApiClient tokenProvider
    // ------------------------------------------------------------------

    @Test
    fun `HttpCoachApiClient tokenProvider defaults to null when not supplied`() {
        val client = HttpCoachApiClient(baseUrl = "http://localhost", apiKey = "key")
        assertNull("tokenProvider must default to null", client.tokenProvider)
    }

    @Test
    fun `HttpCoachApiClient stores a supplied tokenProvider`() {
        val provider: () -> String? = { "my-token" }
        val client = HttpCoachApiClient(
            baseUrl = "http://localhost",
            apiKey = "key",
            tokenProvider = provider,
        )
        assertNotNull("tokenProvider must not be null after being supplied", client.tokenProvider)
    }

    @Test
    fun `HttpCoachApiClient tokenProvider lambda is callable and returns the expected value`() {
        var invoked = false
        val provider: () -> String? = { invoked = true; "bearer-token" }
        val client = HttpCoachApiClient(
            baseUrl = "http://localhost",
            apiKey = "key",
            tokenProvider = provider,
        )
        val token = client.tokenProvider?.invoke()
        assertTrue("tokenProvider lambda must have been invoked", invoked)
        assertEquals("bearer-token", token)
    }

    // ------------------------------------------------------------------
    // 31–35  PlayerProfileDto
    // ------------------------------------------------------------------

    @Test
    fun `PlayerProfileDto retains rating field`() {
        val profile = PlayerProfileDto(rating = 1450.5f, confidence = 0.8f)
        assertEquals(1450.5f, profile.rating, 0.001f)
    }

    @Test
    fun `PlayerProfileDto retains confidence field`() {
        val profile = PlayerProfileDto(rating = 1200.0f, confidence = 0.65f)
        assertEquals(0.65f, profile.confidence, 0.001f)
    }

    @Test
    fun `two identical PlayerProfileDtos are equal`() {
        val a = PlayerProfileDto(rating = 1500.0f, confidence = 0.9f)
        val b = PlayerProfileDto(rating = 1500.0f, confidence = 0.9f)
        assertEquals(a, b)
    }

    @Test
    fun `PlayerProfileDtos differ when rating differs`() {
        val a = PlayerProfileDto(rating = 1500.0f, confidence = 0.9f)
        val b = PlayerProfileDto(rating = 1600.0f, confidence = 0.9f)
        assertNotEquals(a, b)
    }

    @Test
    fun `PlayerProfileDtos differ when confidence differs`() {
        val a = PlayerProfileDto(rating = 1500.0f, confidence = 0.9f)
        val b = PlayerProfileDto(rating = 1500.0f, confidence = 0.5f)
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 36–39  ChatRequestBody with playerProfile and pastMistakes
    // ------------------------------------------------------------------

    @Test
    fun `ChatRequestBody retains non-null playerProfile`() {
        val profile = PlayerProfileDto(rating = 1300.0f, confidence = 0.7f)
        val body = ChatRequestBody(
            fen = "startpos",
            messages = emptyList(),
            playerProfile = profile,
        )
        assertNotNull(body.playerProfile)
        assertEquals(1300.0f, body.playerProfile!!.rating, 0.001f)
        assertEquals(0.7f, body.playerProfile!!.confidence, 0.001f)
    }

    @Test
    fun `ChatRequestBody accepts null playerProfile by default`() {
        val body = ChatRequestBody(fen = "startpos", messages = emptyList())
        assertNull("playerProfile must default to null", body.playerProfile)
    }

    @Test
    fun `ChatRequestBody retains non-null pastMistakes list`() {
        val mistakes = listOf("tactical_vision", "endgame_technique")
        val body = ChatRequestBody(
            fen = "startpos",
            messages = emptyList(),
            pastMistakes = mistakes,
        )
        assertNotNull(body.pastMistakes)
        assertEquals(2, body.pastMistakes!!.size)
        assertEquals("tactical_vision", body.pastMistakes!![0])
        assertEquals("endgame_technique", body.pastMistakes!![1])
    }

    @Test
    fun `ChatRequestBody accepts null pastMistakes by default`() {
        val body = ChatRequestBody(fen = "startpos", messages = emptyList())
        assertNull("pastMistakes must default to null", body.pastMistakes)
    }

    // ------------------------------------------------------------------
    // 40–43  FakeCoachApiClient — player context introspection
    // ------------------------------------------------------------------

    @Test
    fun `FakeCoachApiClient records playerProfile passed to chat`() =
        runBlocking {
            val fake = FakeCoachApiClient()
            val profile = PlayerProfileDto(rating = 1550.0f, confidence = 0.85f)
            fake.chat("fen", emptyList(), playerProfile = profile)
            assertNotNull(fake.lastPlayerProfile)
            assertEquals(1550.0f, fake.lastPlayerProfile!!.rating, 0.001f)
        }

    @Test
    fun `FakeCoachApiClient records pastMistakes passed to chat`() =
        runBlocking {
            val fake = FakeCoachApiClient()
            val mistakes = listOf("pawn_structure", "rook_activity")
            fake.chat("fen", emptyList(), pastMistakes = mistakes)
            assertEquals(mistakes, fake.lastPastMistakes)
        }

    @Test
    fun `chat with null playerProfile is accepted without error`() =
        runBlocking {
            val fake = FakeCoachApiClient()
            fake.chat("fen", emptyList(), playerProfile = null)
            assertNull("null playerProfile must be recorded as null", fake.lastPlayerProfile)
            assertEquals(1, fake.callCount)
        }

    @Test
    fun `chat with empty pastMistakes list is accepted`() =
        runBlocking {
            val fake = FakeCoachApiClient()
            fake.chat("fen", emptyList(), pastMistakes = emptyList())
            assertNotNull("empty list must be recorded, not null", fake.lastPastMistakes)
            assertTrue(fake.lastPastMistakes!!.isEmpty())
        }
}
