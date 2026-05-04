package ai.chesscoach.app

import org.junit.Test
import org.junit.Assert.*

class ChessEngineTest {

    @Test
    fun testPawnInitialMove() {
        val engine = ChessEngine()
        // White pawn at e2 (6,4) to e4 (4,4)
        val success = engine.move(6, 4, 4, 4)
        assertTrue("Pawn should be able to move 2 squares on first move", success)
        assertEquals('P', engine.board[4][4])
        assertEquals('.', engine.board[6][4])
        assertFalse("It should now be black's turn", engine.whiteTurn)
    }

    @Test
    fun testInvalidMove() {
        val engine = ChessEngine()
        // Try to move a white pawn at e2 (6,4) to e5 (3,4) - 3 squares is illegal
        val success = engine.move(6, 4, 3, 4)
        assertFalse("Pawn should not be able to move 3 squares", success)
    }

    // ai_moves_only_once_per_turn is tested in androidTest/ChessNativeInstrumentedTest.kt.
    // It calls ChessNative.getBestMove (an external JNI function) which requires the
    // native library to be loaded — not available in the host JVM test environment.
}
