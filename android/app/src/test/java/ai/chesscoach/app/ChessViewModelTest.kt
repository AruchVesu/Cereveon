package ai.chesscoach.app

import androidx.lifecycle.viewModelScope
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelTest {

    // Explicit scheduler prevents StandardTestDispatcher() from calling
    // getCurrentTestScheduler(), which requires Dispatchers.Main to already be
    // a TestMainDispatcher. Without this, the test fails when another test class
    // runs before it and the process-wide Main dispatcher is in the default state.
    // See the identical pattern and explanation in ChessViewModelEngineFailureTest.
    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    /**
     * Engine that blocks inside getBestMove() on a CountDownLatch until the test
     * calls release(). This pins the Default-thread AI coroutine inside the engine
     * call so the test scheduler's Main queue drains completely before applyAIMove
     * is reached, making the reset-vs-AI-completion race deterministic on all
     * platforms (including fast Linux CI cores that otherwise win the race).
     *
     * The latch has a 5-second hard ceiling so a test-logic bug never hangs CI.
     * @After always calls release() as a safety net even if the test fails early.
     */
    private class BlockingFakeEngine : EngineProvider {
        private val latch = CountDownLatch(1)

        fun release() = latch.countDown()

        override fun getBestMove(fen: String): AIMove {
            latch.await(5, TimeUnit.SECONDS)
            return AIMove(0, 0, 1, 1)
        }
    }

    private lateinit var blockingEngine: BlockingFakeEngine
    private lateinit var viewModel: ChessViewModel

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        blockingEngine = BlockingFakeEngine()
        viewModel = ChessViewModel(blockingEngine)
    }

    @After
    fun tearDown() {
        // Release before resetMain() so any Default-thread continuation that tries
        // withContext(Main) after unblocking sees a live Main dispatcher and exits
        // via CancellationException rather than racing against resetMain().
        blockingEngine.release()
        Dispatchers.resetMain()
    }

    @Test
    fun `test AI move is discarded after reset`() = runTest(testDispatcher) {
        var aiMoveApplied = false

        // 1. Trigger human move — the AI coroutine will eventually block in getBestMove().
        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b" },
            applyAIMove = { _, _, _, _ -> aiMoveApplied = true; '.' },
        )

        // Drain the Main queue. With the blocking engine the Default-thread coroutine
        // stalls inside getBestMove(), so the scheduler empties before applyAIMove is
        // reached and advanceTimeBy returns at a stable point.
        advanceTimeBy(10)

        // 2. Reset while the AI coroutine is blocked — increments stateId, cancels aiJob.
        viewModel.reset()

        // 3. Unblock the engine. The coroutine resumes on Dispatchers.Default, hits
        //    withContext(Main), and is rejected by either the CancellationException from
        //    the cancelled aiJob or the stateId guard. applyAIMove is never called.
        blockingEngine.release()

        // 4. Drain any remaining Main-dispatcher tasks.
        advanceUntilIdle()

        // 5. Verify that the AI move was never applied.
        assertFalse("AI move should have been discarded after reset", aiMoveApplied)

        // Cancel in-flight Dispatchers.Default coroutines before tearDown calls
        // resetMain(), following the same pattern as ChessViewModelEngineFailureTest.
        // Without this, a Default-thread continuation that dispatches to Main after
        // resetMain() races and throws "Dispatchers.Main is used concurrently with
        // setting it", contaminating subsequent test classes.
        viewModel.viewModelScope.cancel()
        advanceUntilIdle()
    }
}
