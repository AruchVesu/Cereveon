package ai.chesscoach.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented test for ChessNative.getBestMove.
 *
 * Must run on a device or emulator so the native library (libchessengine.so)
 * is loaded by the Android runtime. Calling the JNI external function in a
 * host-JVM unit test (src/test/) throws UnsatisfiedLinkError.
 */
@RunWith(AndroidJUnit4::class)
class ChessNativeInstrumentedTest {

    @Test
    fun best_move_is_deterministic_for_same_fen() {
        // Position after 1. e4 — Black to move. Full FEN.
        val fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

        val move1 = ChessNative.getBestMove(fen)
        val move2 = ChessNative.getBestMove(fen)

        assertNotNull("Engine must return a move for a non-terminal position", move1)
        assertNotNull("Second call must also return a move", move2)
        assertEquals(
            "getBestMove must be a pure function: same FEN must always produce the same move",
            move1, move2
        )
    }
}
