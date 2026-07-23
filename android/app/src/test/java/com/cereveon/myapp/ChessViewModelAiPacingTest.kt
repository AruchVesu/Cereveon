package com.cereveon.myapp

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Pins the AI-move "think" pacing: the native engine answers in
 * milliseconds, so [ChessViewModel.requestAIMove] holds every playable
 * reply for 2–3 seconds before it lands on the board.
 *
 * Invariants pinned
 * -----------------
 *  1. A playable reply is HELD for the full pacing window and lands
 *     immediately after it elapses.
 *  2. reset() during the pacing window cancels the held reply — it never
 *     reaches the board.
 *  3. A null engine reply skips pacing entirely: an engine fault hands the
 *     turn back to HUMAN at once instead of pretending to think over a
 *     failure (which would read as a frozen board).
 *  4. The production pacing window is 2–3 seconds (sampled per move).
 *
 * All timing runs on one [TestCoroutineScheduler] (Main AND io), so the
 * 2.5-second holds here are virtual — the suite completes in milliseconds.
 * See ChessViewModelEngineFailureTest for the scheduler-injection pattern.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelAiPacingTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    private class InstantEngine : EngineProvider {
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

    private fun playHumanMove(
        vm: ChessViewModel,
        applyHumanMove: () -> MoveResult = { MoveResult.SUCCESS },
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
    ) {
        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = applyHumanMove,
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1" },
            applyAIMove = applyAIMove,
        )
    }

    @Test
    fun `playable reply is held for the pacing window and lands after it`() = runTest(testDispatcher) {
        var aiMoveApplied = false
        val vm = ChessViewModel(
            InstantEngine(),
            testDispatcher,
            aiThinkPacingMillis = { 2_500L },
        )

        playHumanMove(vm) { _, _, _, _, _ -> aiMoveApplied = true; '.' }
        scheduler.runCurrent()

        // The engine has already answered (it is instant), but the reply
        // must be held for the whole pacing window.
        scheduler.advanceTimeBy(2_499L)
        scheduler.runCurrent()
        assertFalse(
            "engine reply must be held for the full pacing window",
            aiMoveApplied,
        )

        // Crossing the window releases the held reply.
        scheduler.advanceUntilIdle()
        assertTrue(
            "engine reply must land once the pacing window elapses",
            aiMoveApplied,
        )

        vm.viewModelScope.cancel()
        scheduler.advanceUntilIdle()
    }

    @Test
    fun `reset during the pacing window discards the held reply`() = runTest(testDispatcher) {
        var aiMoveApplied = false
        val vm = ChessViewModel(
            InstantEngine(),
            testDispatcher,
            aiThinkPacingMillis = { 2_500L },
        )

        playHumanMove(vm) { _, _, _, _, _ -> aiMoveApplied = true; '.' }
        scheduler.runCurrent()

        // Mid-window reset: cancels aiJob, so the cancellable delay() dies
        // with it and the held reply never reaches the board.
        scheduler.advanceTimeBy(1_000L)
        scheduler.runCurrent()
        vm.reset()
        scheduler.advanceUntilIdle()

        assertFalse(
            "a reply held in the pacing window must be discarded by reset()",
            aiMoveApplied,
        )

        vm.viewModelScope.cancel()
        scheduler.advanceUntilIdle()
    }

    @Test
    fun `null engine reply skips pacing so the turn unfreezes immediately`() = runTest(testDispatcher) {
        // Pacing deliberately enormous: if a null reply were paced, the turn
        // would still be AI at virtual time 0 and the second human move
        // below would be rejected by the `turn != HUMAN` guard.
        var secondHumanMoveAccepted = false
        val vm = ChessViewModel(
            NullEngine(),
            testDispatcher,
            aiThinkPacingMillis = { 600_000L },
        )

        playHumanMove(vm) { _, _, _, _, _ -> '.' }
        scheduler.runCurrent()

        playHumanMove(
            vm,
            applyHumanMove = { secondHumanMoveAccepted = true; MoveResult.SUCCESS },
        ) { _, _, _, _, _ -> '.' }
        scheduler.runCurrent()

        assertTrue(
            "an engine fault must hand the turn back without the think-pacing " +
                "hold — pacing a failure reads as a frozen board",
            secondHumanMoveAccepted,
        )

        vm.viewModelScope.cancel()
        scheduler.advanceUntilIdle()
    }

    @Test
    fun `production pacing window is 2 to 3 seconds sampled per move`() {
        // Product requirement: the opponent takes 2–3 seconds over a move.
        // iOS mirrors this window in PlayViewModel.aiThinkPacing*Nanos.
        assertEquals(2_000L, ChessViewModel.AI_THINK_PACING_MIN_MS)
        assertEquals(3_000L, ChessViewModel.AI_THINK_PACING_MAX_MS)
        repeat(500) {
            val sample = ChessViewModel.defaultAiThinkPacingMillis()
            assertTrue(
                "pacing sample $sample ms fell outside the 2–3s product window",
                sample in ChessViewModel.AI_THINK_PACING_MIN_MS..ChessViewModel.AI_THINK_PACING_MAX_MS,
            )
        }
    }
}
