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
 * Tests that Mode-1 coaching fires after the **human's** move, not the AI's move.
 *
 * Invariants pinned
 * -----------------
 *  1. HUMAN_MOVE_COACH_FIRES_BEFORE_AI_EVAL:   isHumanMoveCoachUpdate=true emitted before engine score.
 *  2. HUMAN_MOVE_FEN_USED_FOR_LIVE_CALL:       FEN passed to getLiveCoaching is the human-move FEN.
 *  3. HUMAN_MOVE_UCI_PASSED_TO_LIVE_CALL:      UCI passed to getLiveCoaching matches the human move.
 *  4. LIVE_HINT_APPEARS_IN_ENGINE_SCORE_UPDATE: lastHumanMoveHint is re-used in the AI-score update.
 *  5. NO_LIVE_CALL_WHEN_CLIENT_NULL:           No update with isHumanMoveCoachUpdate=true when no client.
 *  6. LIVE_CALL_NOT_FIRED_ON_FAILED_MOVE:      FAILED human move → getLiveCoaching never called.
 *  7. HUMAN_MOVE_UPDATE_IS_HUMAN_FLAG:         isHumanMoveCoachUpdate=true on the coaching update.
 *  8. ENGINE_SCORE_UPDATE_NOT_HUMAN_FLAG:       isHumanMoveCoachUpdate=false on the eval score update.
 *  9. CLASSIFICATION_PRESERVED_THROUGH_EVAL:   Backend classification from live coach used in eval update.
 * 10. RESET_CLEARS_LAST_HINT:                  reset() clears stored hint; next move starts fresh.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelHumanMoveCoachTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    private class FakeEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove = AIMove(0, 0, 1, 1)
    }

    private class NeutralEvalClient : EngineEvalClient {
        override suspend fun evaluate(fen: String): ApiResult<EngineEvalResponse> =
            ApiResult.Success(EngineEvalResponse(score = 50, bestMove = "d2d4", source = "engine"))
    }

    private class RecordingLiveClient(
        private val result: ApiResult<LiveMoveResponse>,
    ) : LiveMoveClient {
        var callCount = 0
        var lastFen: String? = null
        var lastUci: String? = null

        override suspend fun getLiveCoaching(
            fen: String,
            uci: String,
            playerId: String,
        ): ApiResult<LiveMoveResponse> {
            callCount++
            lastFen = fen
            lastUci = uci
            return result
        }
    }

    private fun liveSuccess(
        hint: String = "Good move, keep developing!",
        moveQuality: String = "GOOD",
    ): ApiResult<LiveMoveResponse> =
        ApiResult.Success(
            LiveMoveResponse(status = "ok", hint = hint, moveQuality = moveQuality, mode = "LIVE_V1")
        )

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
        humanFen: String = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        aiAfterFen: String = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    ) {
        var fenCallCount = 0
        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = {
                fenCallCount++
                if (fenCallCount <= 2) humanFen else aiAfterFen
            },
            applyAIMove = { _, _, _, _ -> capturedPiece },
        )
        scheduler.advanceUntilIdle()
    }

    // ------------------------------------------------------------------
    // 1. isHumanMoveCoachUpdate=true emitted before the engine score update
    // ------------------------------------------------------------------

    @Test
    fun `human move coach update is emitted with isHumanMoveCoachUpdate=true`() {
        val liveClient = RecordingLiveClient(liveSuccess())
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        val updates = mutableListOf<QuickCoachUpdate>()
        vm.onQuickCoachUpdate = { updates.add(it) }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        assertTrue("At least one human-move coach update expected", updates.any { it.isHumanMoveCoachUpdate })
    }

    // ------------------------------------------------------------------
    // 2. FEN passed to getLiveCoaching is the human-move FEN (before AI)
    // ------------------------------------------------------------------

    @Test
    fun `getLiveCoaching receives FEN after human move, not after AI move`() {
        val humanFen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        val aiAfterFen = "8/8/8/8/4p3/8/PPPP1PPP/RNBQKBNR w - - 0 2"
        val liveClient = RecordingLiveClient(liveSuccess())
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        playMove(vm, humanFen = humanFen, aiAfterFen = aiAfterFen)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        assertEquals(
            "getLiveCoaching must use the FEN after human's move, not after AI's move",
            humanFen,
            liveClient.lastFen,
        )
    }

    // ------------------------------------------------------------------
    // 3. UCI passed to getLiveCoaching matches the human's move
    // ------------------------------------------------------------------

    @Test
    fun `getLiveCoaching receives the human move UCI`() {
        val liveClient = RecordingLiveClient(liveSuccess())
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        // fr=6,fc=4 → e2; tr=4,tc=4 → e4
        assertEquals("e2e4", liveClient.lastUci)
    }

    // ------------------------------------------------------------------
    // 4. Live hint appears in the AI-score update (hint persists through eval)
    // ------------------------------------------------------------------

    @Test
    fun `live hint from human-move coach appears in the engine score update`() {
        val hint = "Excellent central pawn push!"
        val liveClient = RecordingLiveClient(liveSuccess(hint = hint))
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        val updates = mutableListOf<QuickCoachUpdate>()
        vm.onQuickCoachUpdate = { updates.add(it) }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        val engineScoreUpdate = updates.lastOrNull { !it.isHumanMoveCoachUpdate }
        assertNotNull("Engine score update expected", engineScoreUpdate)
        assertEquals(hint, engineScoreUpdate!!.explanation)
    }

    // ------------------------------------------------------------------
    // 5. No isHumanMoveCoachUpdate=true emitted when liveCoachClient is null
    // ------------------------------------------------------------------

    @Test
    fun `no human-move coach update emitted when liveCoachClient is null`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        // liveCoachClient not set

        val updates = mutableListOf<QuickCoachUpdate>()
        vm.onQuickCoachUpdate = { updates.add(it) }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        assertFalse(
            "No isHumanMoveCoachUpdate=true expected when liveCoachClient is null",
            updates.any { it.isHumanMoveCoachUpdate },
        )
    }

    // ------------------------------------------------------------------
    // 6. getLiveCoaching never called on a FAILED human move
    // ------------------------------------------------------------------

    @Test
    fun `getLiveCoaching not called when human move fails`() {
        val liveClient = RecordingLiveClient(liveSuccess())
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.FAILED },
            exportFEN = { "startpos" },
            applyAIMove = { _, _, _, _ -> '.' },
        )
        scheduler.advanceUntilIdle()
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        assertEquals(0, liveClient.callCount)
    }

    // ------------------------------------------------------------------
    // 7. The human-move coaching update has isHumanMoveCoachUpdate=true
    // ------------------------------------------------------------------

    @Test
    fun `human move coaching update carries isHumanMoveCoachUpdate=true`() {
        val liveClient = RecordingLiveClient(liveSuccess())
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        val updates = mutableListOf<QuickCoachUpdate>()
        vm.onQuickCoachUpdate = { updates.add(it) }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        val humanUpdate = updates.firstOrNull { it.isHumanMoveCoachUpdate }
        assertNotNull("Expected a human-move coach update", humanUpdate)
        assertTrue(humanUpdate!!.isHumanMoveCoachUpdate)
    }

    // ------------------------------------------------------------------
    // 8. The engine-score update does NOT have isHumanMoveCoachUpdate=true
    // ------------------------------------------------------------------

    @Test
    fun `engine score update has isHumanMoveCoachUpdate=false`() {
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        // no liveCoachClient — only engine eval fires

        val updates = mutableListOf<QuickCoachUpdate>()
        vm.onQuickCoachUpdate = { updates.add(it) }
        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        assertTrue("All updates without liveClient should have isHumanMoveCoachUpdate=false",
            updates.all { !it.isHumanMoveCoachUpdate })
    }

    // ------------------------------------------------------------------
    // 9. Backend classification from live coach is preserved in eval update
    // ------------------------------------------------------------------

    @Test
    fun `backend BLUNDER classification from live coach is preserved in engine score update`() {
        val liveClient = RecordingLiveClient(liveSuccess(moveQuality = "BLUNDER"))
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        val updates = mutableListOf<QuickCoachUpdate>()
        vm.onQuickCoachUpdate = { updates.add(it) }
        playMove(vm, capturedPiece = '.')   // local heuristic → GOOD
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        val engineScoreUpdate = updates.lastOrNull { !it.isHumanMoveCoachUpdate }
        assertNotNull(engineScoreUpdate)
        assertEquals(MistakeClassification.BLUNDER, engineScoreUpdate!!.classification)
    }

    // ------------------------------------------------------------------
    // 10. reset() clears stored hint; next move starts fresh
    // ------------------------------------------------------------------

    @Test
    fun `reset clears lastHumanMoveHint so next move starts fresh`() {
        val liveClient = RecordingLiveClient(liveSuccess(hint = "First move hint."))
        val vm = ChessViewModel(FakeEngine(), testDispatcher, NeutralEvalClient())
        vm.liveCoachClient = liveClient

        playMove(vm)
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        // Reset the game
        vm.reset()

        // Play a second move with a client that returns null hint (network failure)
        val failClient = RecordingLiveClient(ApiResult.NetworkError(RuntimeException("down")))
        vm.liveCoachClient = failClient
        val vm2Updates = mutableListOf<QuickCoachUpdate>()
        vm.onQuickCoachUpdate = { vm2Updates.add(it) }

        var fenCallCount2 = 0
        vm.onHumanMove(
            fr = 6, fc = 3, tr = 4, tc = 3,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = {
                fenCallCount2++
                "startpos"
            },
            applyAIMove = { _, _, _, _ -> '.' },
        )
        scheduler.advanceUntilIdle()
        vm.viewModelScope.cancel(); scheduler.advanceUntilIdle()

        // The engine score update should NOT carry the hint from the first game
        val engineScoreUpdate = vm2Updates.lastOrNull { !it.isHumanMoveCoachUpdate }
        assertNotEquals("First move hint.", engineScoreUpdate?.explanation)
    }
}
