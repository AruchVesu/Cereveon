package ai.chesscoach.app

import android.view.ContextThemeWrapper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented state tests for [ChessBoardView.loadPosition] — the full
 * position re-seed the puzzle surfaces use (PuzzleTrainerBottomSheet,
 * TodaysDrillBottomSheet, MistakeReplayBottomSheet).
 *
 * Why instrumented: [ChessBoardView] is a real View, so its state can only
 * be exercised on an Android runtime (host-JVM tests cover the pure FEN
 * parsers in [ChessBoardViewPositionTest]; this suite covers the latch and
 * flag *behaviour* those parsers feed).  The view is used unattached, same
 * as [AtriumLayoutInflationTest]'s inflations — `invalidate()` and haptics
 * are no-ops without a window.
 *
 * These pin the fix for the launch bug "the puzzle board freezes after a
 * few tries" (PR #406): a board REUSED across unrelated positions inherited
 * the previous position's game-over latch (a solved mate or a stalemating
 * wrong try), spent castling flags, and en-passant target, because bare
 * [ChessBoardView.setFEN] deliberately preserves live-game state.
 *
 * Invariants pinned
 * -----------------
 *  1. LATCH_SETS_ON_MATE          a mating applyMove latches gameOver and
 *                                 records WHITE_WINS; the board then hard-
 *                                 rejects every further move.
 *  2. SETFEN_KEEPS_LATCH          bare setFEN does NOT clear the latch —
 *                                 the documented live-game contract that
 *                                 made puzzle surfaces freeze.
 *  3. LOADPOSITION_CLEARS_LATCH   loadPosition clears the latch and the
 *                                 pending result; moves apply again (the
 *                                 freeze regression).
 *  4. STALEMATE_LATCH_CLEARED     the wrong-try-that-stalemates variant:
 *                                 DRAW recorded, latch cleared the same way.
 *  5. CASTLING_RIGHTS_FROM_FEN    loadPosition grants castling per the
 *                                 FEN's rights field, denies on "-", and
 *                                 repairs flags a previous position spent
 *                                 (which setFEN leaves broken).
 *  6. EP_TARGET_FROM_FEN          loadPosition arms en passant only when
 *                                 the FEN carries a target square.
 *  7. WALK_SEQUENCE_ROUNDTRIP     the multi-move drill's board choreography:
 *                                 user move -> scripted reply (applyAIMove)
 *                                 -> exportFEN -> loadPosition round-trip ->
 *                                 next user move.
 *
 * Board coordinates: row 0 = rank 8, col 0 = file a (row = 8 - rank,
 * col = file - 'a').
 */
@RunWith(AndroidJUnit4::class)
class ChessBoardViewStateInstrumentedTest {

    private lateinit var board: ChessBoardView

    @Before
    fun createBoard() {
        val themed = ContextThemeWrapper(
            InstrumentationRegistry.getInstrumentation().targetContext,
            R.style.Theme_Cereveon_Atrium,
        )
        board = ChessBoardView(themed)
    }

    // FENs verified with python-chess: Qh7 is mate; Qb3 is stalemate.
    private val mateInOneFen = "7k/8/6KQ/8/8/8/8/8 w - - 0 1"       // corpus et_001
    private val stalemateTrapFen = "8/8/8/8/8/2Q5/2K5/k7 w - - 0 1"
    private val castleFen = "4k3/8/8/8/8/8/8/4K2R w K - 0 1"
    private val castleFenNoRights = "4k3/8/8/8/8/8/8/4K2R w - - 0 1"
    private val enPassantFen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
    private val enPassantFenNoTarget = "4k3/8/8/3pP3/8/8/8/4K3 w - - 0 1"
    private val startposFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    /** Qh6-h7# on the mate-in-one position: (row 2, col 7) -> (row 1, col 7). */
    private fun playMate(): MoveResult = board.applyMove(2, 7, 1, 7)

    @Test
    fun latchSetsOnMate_boardRejectsEveryFurtherMove() {
        board.loadPosition(mateInOneFen)
        assertEquals(MoveResult.SUCCESS, playMate())
        assertEquals(GameResult.WHITE_WINS, board.consumePendingGameOver())
        // Black is mated; but even a WHITE-piece move is rejected outright —
        // the gameOver latch short-circuits before any legality check.
        assertEquals(MoveResult.FAILED, board.applyMove(1, 7, 0, 7))
    }

    @Test
    fun setFenKeepsLatch_theLiveGameContractThatFrozePuzzles() {
        board.loadPosition(mateInOneFen)
        assertEquals(MoveResult.SUCCESS, playMate())
        // Bare setFEN restores the pieces but NOT the latch — this is the
        // deliberate live-game behaviour (review scrubbing must not revive
        // a finished game).  Puzzle surfaces used to call this and freeze.
        board.setFEN(mateInOneFen)
        assertEquals(MoveResult.FAILED, playMate())
    }

    @Test
    fun loadPositionClearsLatch_theFreezeRegression() {
        board.loadPosition(mateInOneFen)
        assertEquals(MoveResult.SUCCESS, playMate())
        // Re-seed the SAME position (a retry) — the latch and the pending
        // result must both clear, exactly what "Next puzzle" / a wrong-move
        // reset relies on.
        board.loadPosition(mateInOneFen)
        assertNull(
            "loadPosition must clear the pending game-over result",
            board.consumePendingGameOver(),
        )
        assertEquals(MoveResult.SUCCESS, playMate())
    }

    @Test
    fun stalemateLatchAlsoCleared_theWrongTryVariant() {
        board.loadPosition(stalemateTrapFen)
        // Qc3-b3 stalemates Black (a wrong try in a winning position).
        assertEquals(MoveResult.SUCCESS, board.applyMove(5, 2, 5, 1))
        assertEquals(GameResult.DRAW, board.consumePendingGameOver())
        assertEquals(MoveResult.FAILED, board.applyMove(5, 1, 5, 2))
        // The retry reset must revive the board.
        board.loadPosition(stalemateTrapFen)
        assertEquals(MoveResult.SUCCESS, board.applyMove(5, 2, 5, 1))
    }

    @Test
    fun castlingRightsComeFromTheFen() {
        // "K" grants White short castling: e1 -> g1 applies.
        board.loadPosition(castleFen)
        assertEquals(MoveResult.SUCCESS, board.applyMove(7, 4, 7, 6))
        // "-" denies it on the identical piece placement.
        board.loadPosition(castleFenNoRights)
        assertEquals(MoveResult.FAILED, board.applyMove(7, 4, 7, 6))
    }

    @Test
    fun loadPositionRepairsSpentCastlingFlags_setFenDoesNot() {
        board.loadPosition(castleFen)
        // Spend the king's flag with a non-castling move (Ke1-e2).
        assertEquals(MoveResult.SUCCESS, board.applyMove(7, 4, 6, 4))
        // Bare setFEN restores the diagram but the spent flag survives, so
        // the castle is refused — the cross-puzzle leak the trainer had.
        board.setFEN(castleFen)
        assertEquals(MoveResult.FAILED, board.applyMove(7, 4, 7, 6))
        // loadPosition re-derives rights from the FEN: the castle is back.
        board.loadPosition(castleFen)
        assertEquals(MoveResult.SUCCESS, board.applyMove(7, 4, 7, 6))
    }

    @Test
    fun enPassantTargetComesFromTheFen() {
        // "d6" arms the capture: e5xd6 e.p. applies (e5=(3,4) -> d6=(2,3)).
        board.loadPosition(enPassantFen)
        assertEquals(MoveResult.SUCCESS, board.applyMove(3, 4, 2, 3))
        // Without a target the same diagonal push to an empty square is illegal.
        board.loadPosition(enPassantFenNoTarget)
        assertEquals(MoveResult.FAILED, board.applyMove(3, 4, 2, 3))
    }

    @Test
    fun walkSequenceRoundTrip_userMoveReplyExportReload() {
        // The multi-move drill choreography both walk surfaces perform:
        // the user's move applies, the scripted opponent reply auto-plays
        // via applyAIMove, the new decision point is captured with
        // exportFEN, and a wrong-try reset loadPosition()s that FEN.
        board.loadPosition(startposFen)
        assertEquals(MoveResult.SUCCESS, board.applyMove(6, 4, 4, 4)) // e2e4
        board.applyAIMove(1, 4, 3, 4)                                  // e7e5 reply
        val midWalkFen = board.exportFEN()
        assertTrue(
            "after the reply it is White's move again: $midWalkFen",
            midWalkFen.contains(" w "),
        )
        board.loadPosition(midWalkFen)
        assertEquals(MoveResult.SUCCESS, board.applyMove(7, 6, 5, 5)) // g1f3
    }
}
