package ai.chesscoach.app

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Unit tests for [ChessViewModel] promotion handling — the ViewModel half
 * of the two AI-promotion / human-promotion-game-over fixes.  (The board
 * half lives in [ChessBoardView] and is exercised by the instrumented
 * suite, since it is an Android View.)
 *
 * Invariants pinned
 * -----------------
 *  PROMO_AIMOVE_CHAR:        AIMove.promoChar decodes the engine's promo code
 *                            ('Q'/'R'/'B'/'N' → letter; 0/junk → ' ').
 *  PROMO_AI_THREADED:        an AI promotion threads the chosen piece into
 *                            applyAIMove (not the human dialog) AND records
 *                            the UCI with its promotion suffix in the PGN.
 *  PROMO_HUMAN_GAMEOVER:     a human promotion that ends the game fires
 *                            onGameOver and does NOT dispatch an AI reply.
 *  PROMO_HUMAN_CONTINUES:    a human promotion that does NOT end the game
 *                            still hands the turn to the AI (behaviour kept).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelPromotionTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    private lateinit var viewModel: ChessViewModel

    /** Black pawn a2→a1 promoting to the given piece: UCI "a2a1<p>". */
    private class PromotingEngine(private val promoPiece: Char) : EngineProvider {
        override fun getBestMove(fen: String): AIMove =
            AIMove(fr = 6, fc = 0, tr = 7, tc = 0, promo = promoPiece.code)
    }

    private fun newViewModel(engine: EngineProvider) {
        viewModel = ChessViewModel(engine, testDispatcher)
    }

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        newViewModel(PromotingEngine('Q'))
    }

    @After
    fun tearDown() {
        viewModel.viewModelScope.cancel()
        scheduler.advanceUntilIdle()
        Dispatchers.resetMain()
    }

    @Test
    fun `PROMO_AIMOVE_CHAR - promoChar decodes engine promo codes`() {
        assertEquals('Q', AIMove(0, 0, 1, 1, 'Q'.code).promoChar())
        assertEquals('N', AIMove(0, 0, 1, 1, 'n'.code).promoChar()) // normalised to upper
        assertEquals(' ', AIMove(0, 0, 1, 1, 0).promoChar())        // no promotion
        assertEquals(' ', AIMove(0, 0, 1, 1, 'x'.code).promoChar()) // junk → none
    }

    @Test
    fun `PROMO_AI_THREADED - AI promotion piece reaches applyAIMove and the PGN`() {
        var promoSeenByBoard: Char? = null
        viewModel.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b" },
            applyAIMove = { _, _, _, _, promo -> promoSeenByBoard = promo; '.' },
        )
        scheduler.advanceUntilIdle()

        // The engine's Queen promotion is forwarded to the board (NOT the
        // human dialog), and the move is recorded with its "q" suffix so
        // the exported PGN / server winner-derivation is complete.
        assertEquals('Q', promoSeenByBoard)
        assertTrue(
            "PGN should carry the promotion suffix; was: ${viewModel.exportPGN()}",
            viewModel.exportPGN().contains("a2a1q"),
        )
    }

    @Test
    fun `PROMO_HUMAN_GAMEOVER - a game-ending human promotion fires onGameOver and skips the AI`() {
        var reported: GameResult? = null
        var aiRequested = false
        viewModel.onGameOver = { reported = it }

        // turn starts HUMAN; simulate the board reporting the promotion as
        // the mating move (consumeGameOver returns a decisive result).
        viewModel.onPromotionFinished(
            exportFEN = { "8/8/8/8/8/8/8/k1K1Q3 b" },
            applyAIMove = { _, _, _, _, _ -> aiRequested = true; '.' },
            consumeGameOver = { GameResult.WHITE_WINS },
        )
        scheduler.advanceUntilIdle()

        assertEquals(GameResult.WHITE_WINS, reported)
        assertFalse("no AI reply may be dispatched in a finished position", aiRequested)
    }

    @Test
    fun `PROMO_HUMAN_CONTINUES - a non-terminal human promotion still hands over to the AI`() {
        var aiRequested = false
        viewModel.onGameOver = { }

        viewModel.onPromotionFinished(
            exportFEN = { "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b" },
            applyAIMove = { _, _, _, _, _ -> aiRequested = true; '.' },
            consumeGameOver = { null },
        )
        scheduler.advanceUntilIdle()

        assertTrue("AI must move after a non-terminal promotion", aiRequested)
    }
}
