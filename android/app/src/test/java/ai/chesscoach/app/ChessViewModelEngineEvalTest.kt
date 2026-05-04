package ai.chesscoach.app

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for ChessViewModel's POST /engine/eval wiring.
 *
 * After every successful AI move the ViewModel must:
 *  - call [EngineEvalClient.evaluate] once with the post-AI FEN
 *  - emit a [QuickCoachUpdate] via [ChessViewModel.onQuickCoachUpdate]
 *    containing the centipawn score and best move from the response
 *  - fall back to "?" when the eval client is null or returns a non-Success result
 *  - preserve the captured-piece classification regardless of the eval result
 *
 * Invariants pinned
 * -----------------
 *  1. ENGINE_EVAL_CALLED_ONCE_AFTER_AI_MOVE:       evaluate() called exactly once per AI move.
 *  2. ENGINE_EVAL_NOT_CALLED_ON_NULL_AI_MOVE:       null AIMove → evaluate() never called.
 *  3. ENGINE_EVAL_NOT_CALLED_ON_FAILED_MOVE:        FAILED human move → evaluate() never called.
 *  4. ON_QCU_ENGINE_SCORE_ON_SUCCESS:               Success → scoreText = formatCentipawns(score).
 *  5. ON_QCU_BEST_MOVE_PROPAGATED:                  bestMove from response ends up in update.
 *  6. ON_QCU_CAPTURED_PIECE_BLUNDER:                capturedPiece='q' → BLUNDER classification.
 *  7. ON_QCU_FALLBACK_ON_HTTP_ERROR:                HttpError → scoreText = "?".
 *  8. ON_QCU_FALLBACK_ON_TIMEOUT:                   Timeout → scoreText = "?".
 *  9. ON_QCU_FALLBACK_ON_NETWORK_ERROR:             NetworkError → scoreText = "?".
 * 10. ON_QCU_FALLBACK_WHEN_NO_EVAL_CLIENT:          null evalClient → scoreText = "?" emitted immediately.
 * 11. ON_QCU_NULL_ENGINE_SCORE_PROPAGATED:          Success with null score → "?".
 * 12. ON_QCU_NULL_BEST_MOVE_ACCEPTED:               null bestMove in response → update.bestMove = null.
 * 13. ENGINE_EVAL_FEN_IS_POST_AI:                   FEN passed to evaluate() is the FEN after AI move.
 * 14. ON_QCU_NOT_FIRED_WHEN_NO_AI_MOVE:             null AIMove → onQuickCoachUpdate never invoked.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelEngineEvalTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    // FakeEngineEvalClient — configurable result, records calls.
    private class FakeEvalClient(
        var nextResult: ApiResult<EngineEvalResponse> = ApiResult.Success(
            EngineEvalResponse(score = 100, bestMove = "e2e4", source = "engine")
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

    private class FakeEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove = AIMove(0, 0, 1, 1)
    }

    private class NullEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove? = null
    }

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    /**
     * Plays one human move with FakeEngine and drains all coroutines.
     *
     * exportFEN() is now called three times per turn:
     *  1. Human-move FEN snapshot for dispatchHumanMoveCoach (fenBeforeAI).
     *  2. FEN for requestAIMove → getBestMove (fenBeforeAI).
     *  3. FEN for dispatchEngineEval → evaluate (fenAfterAI).
     */
    private fun playMove(
        viewModel: ChessViewModel,
        capturedPiece: Char = '.',
        fenBeforeAI: String = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        fenAfterAI: String = fenBeforeAI,
    ) {
        var fenCallCount = 0
        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = {
                fenCallCount++
                if (fenCallCount <= 2) fenBeforeAI else fenAfterAI
            },
            applyAIMove = { _, _, _, _ -> capturedPiece },
        )
        scheduler.advanceUntilIdle()
    }

    // ------------------------------------------------------------------
    // 1. evaluate() called exactly once per AI move
    // ------------------------------------------------------------------

    @Test
    fun `evaluate called once after successful AI move`() {
        val fakeEval = FakeEvalClient()
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertEquals(1, fakeEval.callCount)
    }

    // ------------------------------------------------------------------
    // 2. evaluate() not called when the engine returns null (no move)
    // ------------------------------------------------------------------

    @Test
    fun `evaluate not called when AI engine returns null`() {
        val fakeEval = FakeEvalClient()
        val vm = ChessViewModel(NullEngine(), testDispatcher, fakeEval)
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertEquals(0, fakeEval.callCount)
    }

    // ------------------------------------------------------------------
    // 3. evaluate() not called when the human move fails
    // ------------------------------------------------------------------

    @Test
    fun `evaluate not called on failed human move`() {
        val fakeEval = FakeEvalClient()
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var updateReceived = false
        vm.engineEvalClient = fakeEval
        vm.onQuickCoachUpdate = { updateReceived = true }
        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.FAILED },
            exportFEN = { "startpos" },
            applyAIMove = { _, _, _, _ -> '.' },
        )
        scheduler.advanceUntilIdle()
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertEquals(0, fakeEval.callCount)
        assertFalse("onQuickCoachUpdate must not fire for a failed human move", updateReceived)
    }

    // ------------------------------------------------------------------
    // 4. Success result → scoreText matches formatCentipawns
    // ------------------------------------------------------------------

    @Test
    fun `onQuickCoachUpdate receives centipawn score on engine success`() {
        val fakeEval = FakeEvalClient(
            ApiResult.Success(EngineEvalResponse(score = 152, bestMove = "e2e4", source = "engine"))
        )
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(QuickCoachLogic.formatCentipawns(152), update!!.scoreText)
    }

    // ------------------------------------------------------------------
    // 5. bestMove propagated from the engine response
    // ------------------------------------------------------------------

    @Test
    fun `onQuickCoachUpdate contains bestMove from engine response`() {
        val fakeEval = FakeEvalClient(
            ApiResult.Success(EngineEvalResponse(score = 50, bestMove = "d2d4", source = "engine"))
        )
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals("d2d4", update!!.bestMove)
    }

    // ------------------------------------------------------------------
    // 6. capturedPiece drives the classification (queen → BLUNDER)
    // ------------------------------------------------------------------

    @Test
    fun `capturedPiece queen produces BLUNDER classification in engine update`() {
        val fakeEval = FakeEvalClient(
            ApiResult.Success(EngineEvalResponse(score = -900, bestMove = null, source = "engine"))
        )
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = 'q')
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.BLUNDER, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 7–9. Non-success results fall back to "?" score
    // ------------------------------------------------------------------

    @Test
    fun `onQuickCoachUpdate shows fallback score on HTTP error`() {
        val fakeEval = FakeEvalClient(ApiResult.HttpError(503))
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals("?", update!!.scoreText)
    }

    @Test
    fun `onQuickCoachUpdate shows fallback score on timeout`() {
        val fakeEval = FakeEvalClient(ApiResult.Timeout)
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals("?", update!!.scoreText)
    }

    @Test
    fun `onQuickCoachUpdate shows fallback score on network error`() {
        val fakeEval = FakeEvalClient(ApiResult.NetworkError(RuntimeException("refused")))
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals("?", update!!.scoreText)
    }

    // ------------------------------------------------------------------
    // 10. No eval client → "?" emitted immediately (no network call)
    // ------------------------------------------------------------------

    @Test
    fun `onQuickCoachUpdate shows fallback when no eval client is set`() {
        // engineEvalClient left as null (default)
        val vm = ChessViewModel(FakeEngine(), testDispatcher)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull("Update must be emitted even without an eval client", update)
        assertEquals("?", update!!.scoreText)
    }

    // ------------------------------------------------------------------
    // 11. null score in Success response → "?"
    // ------------------------------------------------------------------

    @Test
    fun `null engine score in success response formats as question mark`() {
        val fakeEval = FakeEvalClient(
            ApiResult.Success(EngineEvalResponse(score = null, bestMove = null, source = "engine"))
        )
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals("?", update!!.scoreText)
    }

    // ------------------------------------------------------------------
    // 12. null bestMove in response → update.bestMove = null
    // ------------------------------------------------------------------

    @Test
    fun `null bestMove in engine response propagates as null in update`() {
        val fakeEval = FakeEvalClient(
            ApiResult.Success(EngineEvalResponse(score = 30, bestMove = null, source = "engine"))
        )
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertNull(update!!.bestMove)
    }

    // ------------------------------------------------------------------
    // 13. FEN passed to evaluate() is the post-AI-move FEN
    // ------------------------------------------------------------------

    @Test
    fun `evaluate receives the FEN after the AI move was applied`() {
        val fakeEval = FakeEvalClient()
        val vm = ChessViewModel(FakeEngine(), testDispatcher, fakeEval)
        val fenAfterAI = "8/8/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
        playMove(vm, fenBeforeAI = "startpos", fenAfterAI = fenAfterAI)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertEquals(
            "evaluate() must receive the FEN after the AI move, not before",
            fenAfterAI,
            fakeEval.lastFen,
        )
    }

    // ------------------------------------------------------------------
    // 14. onQuickCoachUpdate never fired when AI move is null
    // ------------------------------------------------------------------

    @Test
    fun `onQuickCoachUpdate not invoked when AI engine returns null move`() {
        val vm = ChessViewModel(NullEngine(), testDispatcher)
        var fired = false
        vm.onQuickCoachUpdate = { fired = true }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertFalse("onQuickCoachUpdate must not fire when AI move is null", fired)
    }
}
