package ai.chesscoach.app

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
 * Unit tests for [ChessViewModel] PGN / move-history tracking.
 *
 * Uses `testDispatcher` for BOTH [Dispatchers.Main] and the ViewModel's
 * `ioDispatcher`, so every coroutine runs inside the same
 * [TestCoroutineScheduler] and [advanceUntilIdle] drains the entire
 * coroutine graph in one call.
 *
 * The `viewModelScope` is cancelled in every `@After` so that
 * in-flight continuations do not try to dispatch to Main after
 * [Dispatchers.resetMain] is called (which would contaminate later tests).
 *
 * Invariants pinned
 * -----------------
 *  1.  PGN_EMPTY_NEW_GAME:         exportPGN returns "(no moves)" on a fresh ViewModel.
 *  2.  PGN_AFTER_HUMAN_MOVE:       exportPGN contains the human's UCI move after SUCCESS.
 *  3.  PGN_AFTER_FULL_ROUND:       exportPGN contains both human and AI UCI moves.
 *  4.  PGN_MOVE_NUMBERING:         exportPGN move section starts with "1. ".
 *  4b. PGN_HAS_EVENT_HEADER:      exportPGN output starts with [Event PGN header.
 *  5.  PGN_RESET_CLEARS:           exportPGN returns "(no moves)" after reset().
 *  6.  PGN_HUMAN_FAILED_NOT_ADDED: exportPGN unchanged when human move returns FAILED.
 *  7.  PGN_UCI_E2E4:               Human move (row 6,col 4)→(row 4,col 4) encodes as "e2e4".
 *  8.  PGN_AI_UCI_A8B7:            FakeEngine move (0,0)→(1,1) encodes as "a8b7".
 *  9.  PGN_MULTI_RESET:            Multiple resets each clear history independently.
 * 10.  PGN_FORMAT_SINGLE_ROUND:    After one full round move section is "1. e2e4 a8b7".
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelPgnTest {

    // Both main AND io use the same scheduler so advanceUntilIdle() drains all.
    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    private lateinit var viewModel: ChessViewModel

    /** Returns (0,0)→(1,1) which encodes as "a8b7". */
    private class FakeEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove = AIMove(0, 0, 1, 1)
    }

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        viewModel = ChessViewModel(FakeEngine(), testDispatcher)
    }

    @After
    fun tearDown() {
        // Cancel in-flight Default/io coroutines before resetMain() so they
        // cannot dispatch back to Main after it has been torn down.
        viewModel.viewModelScope.cancel()
        advanceUntilIdle()
        Dispatchers.resetMain()
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    private fun advanceUntilIdle() = scheduler.advanceUntilIdle()

    /** Plays one full human+AI round and drains all coroutines. */
    private fun playRound(fr: Int = 6, fc: Int = 4, tr: Int = 4, tc: Int = 4) {
        viewModel.onHumanMove(
            fr = fr, fc = fc, tr = tr, tc = tc,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b" },
            applyAIMove = { _, _, _, _ -> '.' },
        )
        advanceUntilIdle()
    }

    // ------------------------------------------------------------------
    // Tests
    // ------------------------------------------------------------------

    @Test
    fun `exportPGN returns no-moves sentinel on a fresh ViewModel`() {
        assertEquals("(no moves)", viewModel.exportPGN())
    }

    @Test
    fun `exportPGN contains the human move UCI after a successful move`() {
        playRound(fr = 6, fc = 4, tr = 4, tc = 4)
        assertTrue("Expected e2e4 in PGN", viewModel.exportPGN().contains("e2e4"))
    }

    @Test
    fun `exportPGN contains both human and AI UCI moves after a full round`() {
        playRound()
        val pgn = viewModel.exportPGN()
        assertTrue("Expected human move e2e4", pgn.contains("e2e4"))
        assertTrue("Expected AI move a8b7", pgn.contains("a8b7"))
    }

    @Test
    fun `exportPGN output starts with Event PGN header`() {
        playRound()
        assertTrue(
            "Expected PGN to start with [Event header",
            viewModel.exportPGN().startsWith("""[Event "Chess Coach Game"]"""),
        )
    }

    @Test
    fun `exportPGN move section starts with move number prefix`() {
        playRound()
        assertTrue("Expected '1. ' in PGN move section", viewModel.exportPGN().contains("1. "))
    }

    @Test
    fun `exportPGN returns no-moves sentinel after reset`() {
        playRound()
        viewModel.reset()
        assertEquals("(no moves)", viewModel.exportPGN())
    }

    @Test
    fun `exportPGN is unchanged when human move returns FAILED`() {
        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.FAILED },
            exportFEN = { "" },
            applyAIMove = { _, _, _, _ -> '.' },
        )
        advanceUntilIdle()
        assertEquals("(no moves)", viewModel.exportPGN())
    }

    @Test
    fun `uciFromCoords encodes e2-e4 correctly`() {
        // row 6 col 4 = e2, row 4 col 4 = e4
        playRound(fr = 6, fc = 4, tr = 4, tc = 4)
        assertTrue("Expected e2e4", viewModel.exportPGN().contains("e2e4"))
    }

    @Test
    fun `uciFromCoords encodes AI move a8-b7 correctly`() {
        // FakeEngine returns AIMove(fr=0,fc=0,tr=1,tc=1) → a8b7
        playRound()
        assertTrue("Expected a8b7", viewModel.exportPGN().contains("a8b7"))
    }

    @Test
    fun `multiple resets each clear history independently`() {
        playRound()
        viewModel.reset()
        assertEquals("(no moves)", viewModel.exportPGN())

        playRound(fr = 6, fc = 3, tr = 4, tc = 3) // d2d4
        viewModel.reset()
        assertEquals("(no moves)", viewModel.exportPGN())
    }

    @Test
    fun `after one full round PGN has correct format with both moves`() {
        playRound() // human: e2e4 (fr=6,fc=4,tr=4,tc=4), AI: a8b7
        val pgn = viewModel.exportPGN()
        // Headers must be present (P0-A fix: backend requires PGN headers)
        assertTrue("Expected [Event header", pgn.contains("""[Event "Chess Coach Game"]"""))
        // Move section must be correctly formatted
        assertTrue("Expected move line '1. e2e4 a8b7'", pgn.contains("1. e2e4 a8b7"))
    }
}
