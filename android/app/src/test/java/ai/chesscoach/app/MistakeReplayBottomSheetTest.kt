package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pure-Kotlin unit tests for the static helpers on
 * [MistakeReplayBottomSheet.Companion].  The fragment itself extends
 * [androidx.fragment.app.DialogFragment] and can't be instantiated
 * on the host JVM without Robolectric — these tests cover the
 * formatting + coordinate-conversion logic that drives the
 * mistake-replay UI without standing up an Activity.
 *
 * Invariants pinned
 * -----------------
 *  1. ROWCOL_TO_UCI_E2          ChessBoardView (row=6, col=4) → "e2".
 *  2. ROWCOL_TO_UCI_E7          (row=1, col=4) → "e7" (file e, rank 7).
 *  3. ROWCOL_TO_UCI_A1          (row=7, col=0) → "a1".
 *  4. ROWCOL_TO_UCI_H8          (row=0, col=7) → "h8".
 *  5. FORMAT_KICKER             "Mistake · Move 14".
 *  6. FORMAT_PLAYED_MOVE        "You played e2e4 — eval dropped by 240 cp."
 *  7. SOURCE_TYPE_CONSTANT      Companion constant matches the server-side string.
 */
class MistakeReplayBottomSheetTest {

    // ── rowColToUci ──────────────────────────────────────────────────

    @Test
    fun `rowColToUci converts e2`() {
        // ChessBoardView uses (row, col) where row 0 = rank 8, col 0 = file a.
        // White's e2 pawn sits at row=6 (8 - 2), col=4 (file 'e').
        assertEquals("e2", MistakeReplayBottomSheet.rowColToUci(6, 4))
    }

    @Test
    fun `rowColToUci converts e7`() {
        assertEquals("e7", MistakeReplayBottomSheet.rowColToUci(1, 4))
    }

    @Test
    fun `rowColToUci handles board corners`() {
        // a1 (white queen-side rook home) = row 7, col 0.
        assertEquals("a1", MistakeReplayBottomSheet.rowColToUci(7, 0))
        // h8 (black king-side rook home) = row 0, col 7.
        assertEquals("h8", MistakeReplayBottomSheet.rowColToUci(0, 7))
    }

    @Test
    fun `rowColToUci builds full UCI move when concatenated`() {
        // White e2-e4: from (6,4) to (4,4).  Concatenation gives "e2e4".
        val from = MistakeReplayBottomSheet.rowColToUci(6, 4)
        val to = MistakeReplayBottomSheet.rowColToUci(4, 4)
        assertEquals("e2e4", "$from$to")
    }

    // ── formatKicker ─────────────────────────────────────────────────

    @Test
    fun `formatKicker renders mistake move number`() {
        assertEquals("Mistake · Move 1", MistakeReplayBottomSheet.formatKicker(1))
        assertEquals("Mistake · Move 14", MistakeReplayBottomSheet.formatKicker(14))
        assertEquals("Mistake · Move 99", MistakeReplayBottomSheet.formatKicker(99))
    }

    // ── formatPlayedMoveLine ─────────────────────────────────────────

    @Test
    fun `formatPlayedMoveLine includes UCI and cp loss`() {
        // The "240 cp" magnitude is what the user-visible message reads;
        // pinning the format here lets future XP-curve tuning rephrase
        // the line without touching every call site.
        assertEquals(
            "You played e2e4 — eval dropped by 240 cp.",
            MistakeReplayBottomSheet.formatPlayedMoveLine("e2e4", 240),
        )
    }

    @Test
    fun `formatPlayedMoveLine handles promotion UCI`() {
        // Promotion moves are 5-char UCI ("e7e8q").  The line just
        // interpolates the string verbatim — no special handling.
        assertEquals(
            "You played e7e8q — eval dropped by 800 cp.",
            MistakeReplayBottomSheet.formatPlayedMoveLine("e7e8q", 800),
        )
    }

    // ── Source-type constant ─────────────────────────────────────────

    @Test
    fun `source type constant matches server contract`() {
        // The server-side SOURCE_TYPE_MISTAKE_REPLAY constant lives in
        // llm/seca/training/models.py; the Android client must use the
        // exact same string or /training/solve will 400.  Pinning here
        // catches a future copy-paste typo on either side.
        assertEquals(
            "mistake_replay",
            MistakeReplayBottomSheet.SOURCE_TYPE_MISTAKE_REPLAY,
        )
    }
}
