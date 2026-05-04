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
 * Tests that the backend move_quality from POST /live/move overrides the local
 * material-capture heuristic in [ChessViewModel].
 *
 * Invariants pinned
 * -----------------
 *  1. BACKEND_BLUNDER_OVERRIDES_GOOD_CAPTURE:   Backend "BLUNDER" → BLUNDER even when no piece captured.
 *  2. BACKEND_GOOD_OVERRIDES_QUEEN_CAPTURE:     Backend "GOOD" → GOOD even when queen captured.
 *  3. BACKEND_MISTAKE_OVERRIDES_PAWN_CAPTURE:   Backend "MISTAKE" → MISTAKE for pawn-level capture.
 *  4. BACKEND_INACCURACY_MAPS_CORRECTLY:        Backend "INACCURACY" → INACCURACY.
 *  5. LIVE_FAILURE_FALLS_BACK_TO_LOCAL:         Network failure → local classifyCapture used.
 *  6. NO_LIVE_CLIENT_FALLS_BACK_TO_LOCAL:       No liveCoachClient → local classifyCapture used.
 *  7. BLANK_MOVE_QUALITY_FALLS_BACK_TO_LOCAL:   Blank moveQuality string → local classifyCapture used.
 *  8. UNKNOWN_MOVE_QUALITY_MAPS_TO_GOOD:        Unknown backend string → GOOD (fail-safe).
 *  9. FROM_BACKEND_STRING_CASE_INSENSITIVE:     "blunder" (lowercase) → BLUNDER.
 * 10. FROM_BACKEND_STRING_ALL_VARIANTS:         All four canonical strings map correctly.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelLiveMoveQualityTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    private class FakeEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove = AIMove(0, 0, 1, 1)
    }

    /** Eval client that always succeeds with a neutral score. */
    private class NeutralEvalClient : EngineEvalClient {
        override suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse> =
            ApiResult.Success(EngineEvalResponse(score = 0, bestMove = null, source = "engine"))
    }

    private class FakeLiveMoveClient(
        private val result: ApiResult<LiveMoveResponse>,
    ) : LiveMoveClient {
        override suspend fun getLiveCoaching(
            fen: String,
            uci: String,
            playerId: String,
        ): ApiResult<LiveMoveResponse> = result
    }

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun playMove(
        vm: ChessViewModel,
        capturedPiece: Char = '.',
    ) {
        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "startpos" },
            applyAIMove = { _, _, _, _ -> capturedPiece },
        )
        scheduler.advanceUntilIdle()
    }

    private fun liveSuccess(moveQuality: String, hint: String = "Watch out!"): ApiResult<LiveMoveResponse> =
        ApiResult.Success(LiveMoveResponse(status = "ok", hint = hint, moveQuality = moveQuality, mode = "LIVE_V1"))

    // ------------------------------------------------------------------
    // 1. Backend BLUNDER overrides good (no-capture) local classification
    // ------------------------------------------------------------------

    @Test
    fun `backend BLUNDER overrides local GOOD classification when no piece captured`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = FakeLiveMoveClient(liveSuccess("BLUNDER"))
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = '.')   // local heuristic → GOOD
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.BLUNDER, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 2. Backend GOOD overrides local BLUNDER (queen captured)
    // ------------------------------------------------------------------

    @Test
    fun `backend GOOD overrides local BLUNDER even when queen was captured`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = FakeLiveMoveClient(liveSuccess("GOOD"))
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = 'q')   // local heuristic → BLUNDER
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.GOOD, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 3. Backend MISTAKE overrides pawn-level (INACCURACY) local result
    // ------------------------------------------------------------------

    @Test
    fun `backend MISTAKE overrides local INACCURACY for pawn capture`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = FakeLiveMoveClient(liveSuccess("MISTAKE"))
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = 'p')   // local heuristic → INACCURACY
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.MISTAKE, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 4. Backend INACCURACY maps correctly
    // ------------------------------------------------------------------

    @Test
    fun `backend INACCURACY classification maps correctly`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = FakeLiveMoveClient(liveSuccess("INACCURACY"))
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = '.')
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.INACCURACY, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 5. Live network failure → falls back to local classifyCapture
    // ------------------------------------------------------------------

    @Test
    fun `network failure on live move falls back to local capture classification`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = FakeLiveMoveClient(ApiResult.NetworkError(RuntimeException("timeout")))
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = 'q')   // local heuristic → BLUNDER
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.BLUNDER, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 6. No live client → falls back to local classifyCapture
    // ------------------------------------------------------------------

    @Test
    fun `no live client falls back to local capture classification`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        // liveCoachClient left null
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = 'r')   // local heuristic → MISTAKE
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.MISTAKE, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 7. Blank moveQuality string → falls back to local classifyCapture
    // ------------------------------------------------------------------

    @Test
    fun `blank moveQuality from backend falls back to local capture classification`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = FakeLiveMoveClient(liveSuccess("", hint = "hint"))
        var update: QuickCoachUpdate? = null
        vm.onQuickCoachUpdate = { update = it }
        playMove(vm, capturedPiece = 'q')   // local heuristic → BLUNDER
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()
        assertNotNull(update)
        assertEquals(MistakeClassification.BLUNDER, update!!.classification)
    }

    // ------------------------------------------------------------------
    // 8–10. QuickCoachLogic.fromBackendString unit tests
    // ------------------------------------------------------------------

    @Test
    fun `fromBackendString unknown string maps to GOOD`() {
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.fromBackendString("BEST"))
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.fromBackendString("OK"))
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.fromBackendString(""))
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.fromBackendString("EXCELLENT"))
    }

    @Test
    fun `fromBackendString is case-insensitive`() {
        assertEquals(MistakeClassification.BLUNDER, QuickCoachLogic.fromBackendString("blunder"))
        assertEquals(MistakeClassification.MISTAKE, QuickCoachLogic.fromBackendString("mistake"))
        assertEquals(MistakeClassification.INACCURACY, QuickCoachLogic.fromBackendString("Inaccuracy"))
        assertEquals(MistakeClassification.GOOD, QuickCoachLogic.fromBackendString("Good"))
    }

    @Test
    fun `fromBackendString maps all four canonical backend strings correctly`() {
        assertEquals(MistakeClassification.GOOD,       QuickCoachLogic.fromBackendString("GOOD"))
        assertEquals(MistakeClassification.INACCURACY, QuickCoachLogic.fromBackendString("INACCURACY"))
        assertEquals(MistakeClassification.MISTAKE,    QuickCoachLogic.fromBackendString("MISTAKE"))
        assertEquals(MistakeClassification.BLUNDER,    QuickCoachLogic.fromBackendString("BLUNDER"))
    }
}
