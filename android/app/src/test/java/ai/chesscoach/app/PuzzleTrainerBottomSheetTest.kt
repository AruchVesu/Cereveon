package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-Kotlin tests for the static helpers on
 * [PuzzleTrainerBottomSheet.Companion].  The fragment itself extends
 * [androidx.fragment.app.DialogFragment] and can't run on the host
 * JVM without Robolectric — these tests cover the kicker formatting,
 * the FEN side-to-move parsing that drives the board flip, and the
 * /training/solve source_type mirror, without standing up an Activity.
 *
 * Invariants pinned
 * -----------------
 *  1. SOURCE_TYPE_MIRRORS_SERVER   "standard_puzzle" — must byte-match
 *                                  llm.seca.training.models.SOURCE_TYPE_STANDARD_PUZZLE
 *                                  or the server 400s the solve.
 *  2. KICKER_LICHESS_MIX           mix theme collapses; difficulty + attribution shown.
 *  3. KICKER_LIBRARY_THEMED        corpus theme pretty-printed, no Lichess attribution.
 *  4. KICKER_GENERIC_THEME         "generic" collapses like "mix".
 *  5. KICKER_NO_DIFFICULTY         blank difficulty segment dropped.
 *  6. KICKER_BARE_MINIMUM          all-blank optional fields → just "Puzzle".
 *  7. BLACK_TO_MOVE_TRUE           FEN with " b " parses as Black to move (flip).
 *  8. BLACK_TO_MOVE_FALSE          FEN with " w " parses as White to move.
 *  9. BLACK_TO_MOVE_MALFORMED     malformed FEN defaults to White (no flip).
 * 10. SIDE_LABELS                  "White to move" / "Black to move" strings.
 */
class PuzzleTrainerBottomSheetTest {

    // ── source_type mirror ───────────────────────────────────────────

    @Test
    fun `SOURCE_TYPE_MIRRORS_SERVER - standard_puzzle wire constant`() {
        assertEquals(
            "standard_puzzle",
            PuzzleTrainerBottomSheet.SOURCE_TYPE_STANDARD_PUZZLE,
        )
    }

    // ── formatKicker ─────────────────────────────────────────────────

    @Test
    fun `KICKER_LICHESS_MIX - mix collapses, difficulty and attribution shown`() {
        val puzzle = PuzzleNextDto(
            puzzleId = "lichess_AbCd1",
            fen = "8/8/8/8/8/8/8/8 w - - 0 1",
            expectedMoveUci = "e2e4",
            theme = "mix",
            difficulty = "intermediate",
            source = "lichess",
            rating = 1400,
        )
        assertEquals(
            "Puzzle · Intermediate · via Lichess",
            PuzzleTrainerBottomSheet.formatKicker(puzzle),
        )
    }

    @Test
    fun `KICKER_LIBRARY_THEMED - corpus theme pretty-printed, no attribution`() {
        val puzzle = PuzzleNextDto(
            puzzleId = "king_safety_001",
            fen = "8/8/8/8/8/8/8/8 w - - 0 1",
            expectedMoveUci = "e2e4",
            theme = "king_safety",
            difficulty = "beginner",
            source = "library",
            rating = null,
        )
        assertEquals(
            "Puzzle · King safety · Beginner",
            PuzzleTrainerBottomSheet.formatKicker(puzzle),
        )
    }

    @Test
    fun `KICKER_GENERIC_THEME - generic collapses like mix`() {
        val puzzle = PuzzleNextDto(
            theme = "generic",
            difficulty = "advanced",
            source = "library",
        )
        assertEquals(
            "Puzzle · Advanced",
            PuzzleTrainerBottomSheet.formatKicker(puzzle),
        )
    }

    @Test
    fun `KICKER_NO_DIFFICULTY - blank difficulty segment dropped`() {
        val puzzle = PuzzleNextDto(
            theme = "mix",
            difficulty = "",
            source = "lichess",
        )
        assertEquals(
            "Puzzle · via Lichess",
            PuzzleTrainerBottomSheet.formatKicker(puzzle),
        )
    }

    @Test
    fun `KICKER_BARE_MINIMUM - all-blank optional fields`() {
        val puzzle = PuzzleNextDto(theme = "mix", difficulty = "", source = "")
        assertEquals("Puzzle", PuzzleTrainerBottomSheet.formatKicker(puzzle))
    }

    // ── isBlackToMove / sideToMoveLabel ──────────────────────────────

    @Test
    fun `BLACK_TO_MOVE_TRUE - b field flips the board`() {
        assertTrue(
            PuzzleTrainerBottomSheet.isBlackToMove(
                "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
            ),
        )
    }

    @Test
    fun `BLACK_TO_MOVE_FALSE - w field keeps White at the bottom`() {
        assertFalse(
            PuzzleTrainerBottomSheet.isBlackToMove(
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            ),
        )
    }

    @Test
    fun `BLACK_TO_MOVE_MALFORMED - missing side field defaults to White`() {
        assertFalse(PuzzleTrainerBottomSheet.isBlackToMove("not-a-fen"))
        assertFalse(PuzzleTrainerBottomSheet.isBlackToMove(""))
    }

    @Test
    fun `SIDE_LABELS - status strings for both sides`() {
        assertEquals(
            "Black to move",
            PuzzleTrainerBottomSheet.sideToMoveLabel(
                "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
            ),
        )
        assertEquals(
            "White to move",
            PuzzleTrainerBottomSheet.sideToMoveLabel(
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            ),
        )
    }
}
