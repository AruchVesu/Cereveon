package ai.chesscoach.app

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Test

/**
 * Tests for ChessViewModel behaviour when the engine cannot provide a move.
 *
 * Architecture gap documented here
 * ----------------------------------
 * ChessNative.isLibraryLoaded may be false if the native library failed to load
 * at startup (the load error is caught silently in ChessNative.init). However,
 * NativeEngineProvider.getBestMove calls ChessNative.getBestMove without first
 * checking isLibraryLoaded. Calling an external JNI function when the library
 * was not loaded throws UnsatisfiedLinkError, which propagates out of the
 * ViewModel's AI coroutine uncaught. The coroutine's finally block resets
 * aiThinking, but `turn` stays at AI, permanently blocking human input.
 *
 * These tests verify the null-return path — the behaviour a corrected
 * NativeEngineProvider would exhibit if it returned null on isLibraryLoaded=false.
 *
 * Technical note on scheduler
 * ----------------------------
 * runTest is called with the same testDispatcher that was passed to setMain.
 * This ensures that withContext(Dispatchers.Main) dispatches inside the same
 * TestCoroutineScheduler that advanceUntilIdle() drains. Using a separate
 * scheduler for each would leave Main-dispatcher callbacks permanently queued.
 *
 * Note on constructor initialization
 * ------------------------------------
 * StandardTestDispatcher() with no args calls getCurrentTestScheduler(), which
 * requires Dispatchers.Main to already be a TestMainDispatcher. When this class
 * is instantiated before any setMain() call in the process (e.g. when it runs
 * first alphabetically), that call fails with "The main looper is not available".
 * Providing an explicit TestCoroutineScheduler bypasses getCurrentTestScheduler().
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelEngineFailureTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    /**
     * Simulates a corrected NativeEngineProvider that returns null when
     * ChessNative.isLibraryLoaded is false, rather than calling getBestMove
     * (which would throw UnsatisfiedLinkError from the unloaded JNI function).
     */
    private class NullEngineProvider : EngineProvider {
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

    // ------------------------------------------------------------------
    // Null-return path: ViewModel handles null from the engine correctly
    // ------------------------------------------------------------------

    @Test
    fun `null engine result does not apply AI move`() = runTest(testDispatcher) {
        var aiMoveApplied = false
        val viewModel = ChessViewModel(NullEngineProvider())

        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b" },
            applyAIMove = { _, _, _, _ -> aiMoveApplied = true; '.' }
        )

        advanceUntilIdle()

        assertFalse(
            "AI move must not be applied when engine returns null (library not loaded)",
            aiMoveApplied
        )

        // Cancel viewModelScope so any in-flight Dispatchers.Default coroutines that
        // have not yet dispatched back to Main are cancelled before tearDown calls
        // resetMain(). Without this, a Default-thread continuation that dispatches to
        // Main after resetMain() throws and contaminates the next test class.
        viewModel.viewModelScope.cancel()
        advanceUntilIdle()
    }

    @Test
    fun `invalid AIMove is not applied to the board`() = runTest(testDispatcher) {
        // Regression: a non-null but invalid AIMove (fr < 0) must not be applied.
        // isValid() returns fr >= 0; processAIMoveResult guards on !move.isValid().
        class InvalidMoveProvider : EngineProvider {
            override fun getBestMove(fen: String): AIMove = AIMove(-1, -1, -1, -1)
        }

        var aiMoveApplied = false
        val viewModel = ChessViewModel(InvalidMoveProvider())

        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b" },
            applyAIMove = { _, _, _, _ -> aiMoveApplied = true; '.' }
        )

        advanceUntilIdle()

        assertFalse(
            "An invalid AIMove (fr=-1) must not be applied to the board. " +
            "processAIMoveResult must guard on move.isValid() before calling applyAIMove.",
            aiMoveApplied
        )

        // Cancel viewModelScope to stop any in-flight Default-thread coroutines before
        // tearDown resets Main. See note in the first test for details.
        viewModel.viewModelScope.cancel()
        advanceUntilIdle()
    }

    // Note: "null engine result after reset does not apply stale move" is intentionally
    // omitted. That contract (stateId guard discards stale AI results after reset) is
    // already covered by ChessViewModelTest.test AI move is discarded after reset, which
    // uses FakeEngine. Adding it here with NullEngineProvider introduces a race between
    // the cancelled coroutine's Main dispatcher access and @After resetMain(), causing
    // intermittent failures that are not representative of a real bug.
}
