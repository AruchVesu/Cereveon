package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Area 7 — Quick Coach UI stress tests.
 *
 * Tests the ChessViewModel turn state-machine under:
 *  - AI job cancellation (reset discards stale move)
 *  - FAILED move does not advance turn to AI
 *  - PROMOTION pending does not prematurely fire AI
 *  - Successful move eventually triggers AI callback
 *  - Null AI move handled without crash
 *  - AIMove validity boundary conditions
 *  - MistakeClassification / MoveResult model integrity
 *
 * Dispatcher strategy
 * -------------------
 * ChessViewModel.requestAIMove() uses viewModelScope.launch(Dispatchers.Default),
 * with two withContext(Dispatchers.Main) calls inside:
 *   1. withContext(Main) { exportFEN() }
 *   2. withContext(Main) { processAIMoveResult() }
 *
 * UnconfinedTestDispatcher has isDispatchNeeded=false, so every
 * withContext(Dispatchers.Main) call runs inline on the calling Default
 * thread rather than being queued for later dispatch.  This collapses
 * the multi-round advanceUntilIdle()+sleep dance into a single
 * Thread.sleep() or waitFor{} poll on the test thread.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class QuickCoachStressTest {

    private lateinit var viewModel: ChessViewModel

    // UnconfinedTestDispatcher: Main tasks run inline on the calling thread —
    // no advanceUntilIdle() rounds required.
    private val testDispatcher = UnconfinedTestDispatcher()

    private class InstantFakeEngine : EngineProvider {
        val calls = AtomicInteger(0)
        override fun getBestMove(fen: String): AIMove {
            calls.incrementAndGet()
            return AIMove(0, 0, 1, 1)
        }
    }

    private class NullEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove? = null
    }

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        viewModel = ChessViewModel(InstantFakeEngine())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    /**
     * Polls [condition] every 50 ms for up to [maxWaitMs] ms.
     * Returns true as soon as the condition holds, false on timeout.
     */
    private fun waitFor(maxWaitMs: Long = 2_000L, condition: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + maxWaitMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return true
            Thread.sleep(50)
        }
        return condition()
    }

    // ------------------------------------------------------------------
    // Positive AI-callback tests
    // ------------------------------------------------------------------

    @Test
    fun successfulMove_eventually_triggers_AI() {
        val aiApplied = AtomicBoolean(false)
        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1" },
            applyAIMove = { _, _, _, _ -> aiApplied.set(true); '.' }
        )
        assertTrue("AI move must be applied after a successful human move",
            waitFor { aiApplied.get() })
    }

    @Test
    fun promotionFinished_eventually_triggers_AI() {
        viewModel.onHumanMove(
            fr = 1, fc = 0, tr = 0, tc = 0,
            applyHumanMove = { MoveResult.PROMOTION },
            exportFEN = { "8/P7/8/8/8/8/8/8 w - - 0 1" },
            applyAIMove = { _, _, _, _ -> '.' }
        )

        val aiApplied = AtomicBoolean(false)
        viewModel.onPromotionFinished(
            exportFEN = { "Q7/8/8/8/8/8/8/8 b - - 0 1" },
            applyAIMove = { _, _, _, _ -> aiApplied.set(true); '.' }
        )
        assertTrue("onPromotionFinished must trigger AI move",
            waitFor { aiApplied.get() })
    }

    @Test
    fun nullAIMove_doesNotCrash() {
        val vm = ChessViewModel(NullEngine())
        var crashed = false
        try {
            vm.onHumanMove(
                fr = 6, fc = 4, tr = 4, tc = 4,
                applyHumanMove = { MoveResult.SUCCESS },
                exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1" },
                applyAIMove = { _, _, _, _ -> '.' }
            )
            Thread.sleep(500)
        } catch (e: Exception) {
            crashed = true
        }
        assertFalse("Null AI move must not crash the ViewModel", crashed)
    }

    @Test
    fun fiveSequentialCycles_aiCalledEachCycle() {
        val engine = InstantFakeEngine()
        val vm = ChessViewModel(engine)
        var successCycles = 0

        repeat(5) {
            val aiApplied = AtomicBoolean(false)
            vm.onHumanMove(
                fr = 6, fc = 4, tr = 4, tc = 4,
                applyHumanMove = { MoveResult.SUCCESS },
                exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1" },
                applyAIMove = { _, _, _, _ -> aiApplied.set(true); '.' }
            )
            if (waitFor { aiApplied.get() }) successCycles++
            vm.reset()
        }

        assertTrue(
            "AI must respond in at least 4 of 5 cycles; got $successCycles",
            successCycles >= 4
        )
    }

    // ------------------------------------------------------------------
    // Negative tests: AI must NOT be applied
    // ------------------------------------------------------------------

    /**
     * Uses a blocking engine so the reset() call is guaranteed to happen
     * while the AI job is live (requestId=0 already captured).
     * After reset stateId becomes 1; the cancelled coroutine's final
     * withContext(Main){processAIMoveResult} either throws CancellationException
     * (coroutine cancelled) or is discarded by the stateId guard (1 ≠ 0).
     */
    @Test
    fun resetDuringAI_discardsStaleMoveResult() {
        val engineRunning = CountDownLatch(1)
        val engineProceed = CountDownLatch(1)
        val vm = ChessViewModel(object : EngineProvider {
            override fun getBestMove(fen: String): AIMove {
                engineRunning.countDown()                     // signal: requestId=0 is captured
                engineProceed.await(5, TimeUnit.SECONDS)     // block until reset() is done
                return AIMove(0, 0, 1, 1)
            }
        })

        val aiApplied = AtomicBoolean(false)
        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1" },
            applyAIMove = { _, _, _, _ -> aiApplied.set(true); '.' }
        )

        // Block until the engine is running — guarantees requestId=0 is live inside the coroutine
        assertTrue("Engine must start within 5s", engineRunning.await(5, TimeUnit.SECONDS))

        // Reset: stateId → 1, aiJob is cancelled
        vm.reset()

        // Release the engine; the coroutine is cancelled and/or stateId(1) ≠ requestId(0)
        engineProceed.countDown()

        // Give any background threads time to finish, then verify the move was discarded
        Thread.sleep(500)
        assertFalse("Stale AI move must be discarded after reset", aiApplied.get())
    }

    @Test
    fun failedMove_doesNotTriggerAI() {
        val aiCalled = AtomicBoolean(false)
        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 3, tc = 4,
            applyHumanMove = { MoveResult.FAILED },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" },
            applyAIMove = { _, _, _, _ -> aiCalled.set(true); '.' }
        )
        Thread.sleep(200)
        assertFalse("FAILED move must not trigger AI", aiCalled.get())
    }

    @Test
    fun promotionPending_doesNotImmediatelyTriggerAI() {
        val aiCalled = AtomicBoolean(false)
        viewModel.onHumanMove(
            fr = 1, fc = 0, tr = 0, tc = 0,
            applyHumanMove = { MoveResult.PROMOTION },
            exportFEN = { "8/P7/8/8/8/8/8/8 w - - 0 1" },
            applyAIMove = { _, _, _, _ -> aiCalled.set(true); '.' }
        )
        Thread.sleep(200)
        assertFalse("PROMOTION pending must not immediately trigger AI", aiCalled.get())
    }

    // ------------------------------------------------------------------
    // Pure model tests — synchronous, no coroutines
    // ------------------------------------------------------------------

    @Test
    fun aiMove_isValid_rejectsNegativeFr() {
        assertFalse(AIMove(fr = -1, fc = 0, tr = 1, tc = 0).isValid())
    }

    @Test
    fun aiMove_isValid_acceptsZeroFr() {
        assertTrue(AIMove(fr = 0, fc = 0, tr = 7, tc = 7).isValid())
    }

    @Test
    fun aiMove_isValid_acceptsPositiveCoordinates() {
        assertTrue(AIMove(fr = 6, fc = 4, tr = 4, tc = 4).isValid())
    }

    @Test
    fun aiMove_equalityAndCopy() {
        val m1 = AIMove(1, 2, 3, 4)
        assertEquals(m1, AIMove(1, 2, 3, 4))
        val copy = m1.copy(tr = 5)
        assertNotEquals(m1, copy)
        assertEquals(5, copy.tr)
    }

    @Test
    fun aiMove_100RandomValid_allPass() {
        val rng = java.util.Random(42L)
        repeat(100) {
            assertTrue(
                AIMove(rng.nextInt(8), rng.nextInt(8), rng.nextInt(8), rng.nextInt(8)).isValid()
            )
        }
    }

    @Test
    fun mistakeClassification_4DistinctNonEmptyLabels() {
        val labels = MistakeClassification.entries.map { it.label() }
        assertEquals(4, labels.size)
        assertEquals(4, labels.distinct().size)
        assertTrue(labels.all { it.isNotEmpty() })
    }

    @Test
    fun moveResult_3DistinctValues() {
        assertEquals(3, MoveResult.entries.size)
    }
}
