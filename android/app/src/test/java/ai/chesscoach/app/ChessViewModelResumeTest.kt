package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-Kotlin tests for the Resume restore path on
 * [ChessViewModel.restoreMoveHistory] / [ChessViewModel.exportUciHistory]
 * and the [MainActivity.parseUciHistory] companion helper.
 *
 * These are stateless operations on the ViewModel's move-history list
 * and don't touch coroutines / engine / Android framework — they run
 * on the host JVM without the dispatcher gymnastics
 * [ChessViewModelTest] needs for its concurrency tests.
 *
 * Invariants pinned
 * -----------------
 *  1. exportUciHistory roundtrips through parseUciHistory + restoreMoveHistory.
 *  2. exportUciHistory yields "" for a fresh ViewModel.
 *  3. parseUciHistory tolerates null / blank / trailing-comma inputs
 *     so a corrupt prefs value never produces a 1-element list with "".
 *  4. restoreMoveHistory updates moveCount.
 *  5. restoreMoveHistory exposes the restored moves through exportPGN
 *     so /game/finish receives the full pre-resume PGN, not a stub.
 */
class ChessViewModelResumeTest {

    @Test
    fun `exportUciHistory is empty before any moves are made`() {
        val vm = ChessViewModel(NoopEngine())
        assertEquals("", vm.exportUciHistory())
        assertEquals(0, vm.moveCount)
    }

    @Test
    fun `restoreMoveHistory then exportUciHistory roundtrips losslessly`() {
        val vm = ChessViewModel(NoopEngine())
        val moves = listOf("e2e4", "e7e5", "g1f3", "b8c6")
        vm.restoreMoveHistory(moves)

        assertEquals("e2e4,e7e5,g1f3,b8c6", vm.exportUciHistory())
        assertEquals(4, vm.moveCount)
    }

    @Test
    fun `restoreMoveHistory replaces any prior history rather than appending`() {
        val vm = ChessViewModel(NoopEngine())
        vm.restoreMoveHistory(listOf("e2e4", "e7e5"))
        vm.restoreMoveHistory(listOf("d2d4", "d7d5", "c2c4"))

        assertEquals("d2d4,d7d5,c2c4", vm.exportUciHistory())
        assertEquals(3, vm.moveCount)
    }

    @Test
    fun `restoreMoveHistory feeds exportPGN so finish carries the full game`() {
        val vm = ChessViewModel(NoopEngine())
        vm.restoreMoveHistory(listOf("e2e4", "e7e5", "g1f3", "b8c6"))

        val pgn = vm.exportPGN()
        assertTrue("PGN must include all restored moves, got: $pgn",
            pgn.contains("e2e4") && pgn.contains("e7e5") &&
                pgn.contains("g1f3") && pgn.contains("b8c6"))
        assertTrue("PGN must include the mandatory headers",
            pgn.contains("[Event ") && pgn.contains("[White ") &&
                pgn.contains("[Black ") && pgn.contains("[Result "))
    }

    @Test
    fun `parseUciHistory handles nullable inputs without crashing`() {
        assertEquals(emptyList<String>(), MainActivity.parseUciHistory(null))
        assertEquals(emptyList<String>(), MainActivity.parseUciHistory(""))
        assertEquals(emptyList<String>(), MainActivity.parseUciHistory("   "))
    }

    @Test
    fun `parseUciHistory tolerates trailing and stray commas`() {
        // A stale "" from an earlier persist used to yield [""], which
        // then triggered weird "Move 1" displays for an opening.
        assertEquals(emptyList<String>(), MainActivity.parseUciHistory(","))
        assertEquals(emptyList<String>(), MainActivity.parseUciHistory(",,,"))
        assertEquals(listOf("e2e4"), MainActivity.parseUciHistory("e2e4,"))
        assertEquals(listOf("e2e4", "e7e5"), MainActivity.parseUciHistory("e2e4,,e7e5"))
    }

    @Test
    fun `parseUciHistory roundtrips with exportUciHistory`() {
        val vm = ChessViewModel(NoopEngine())
        val moves = listOf("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6")
        vm.restoreMoveHistory(moves)

        val parsed = MainActivity.parseUciHistory(vm.exportUciHistory())
        assertEquals(moves, parsed)
    }

    /** Engine that never gets called — restoreMoveHistory is pure list manipulation. */
    private class NoopEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove? = null
    }
}
