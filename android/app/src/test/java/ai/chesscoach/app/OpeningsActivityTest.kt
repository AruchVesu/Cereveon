package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-Kotlin tests for the static helpers + canonical default
 * repertoire on [OpeningsActivity.Companion].  These run on the host
 * JVM without instrumentation since the helpers don't touch the
 * Android framework.
 *
 * Invariants pinned
 * -----------------
 *  1. DEFAULT_REPERTOIRE has exactly one active line.
 *  2. DEFAULT_REPERTOIRE matches the design's 4 ECO codes.
 *  3. formatMastery clamps to [0, 100] so a corrupt mastery value
 *     never renders as "-25%" or "150%".
 *  4. formatMastery rounds half-up to a whole percent.
 *  5. formatAvgDepth returns "0" for an empty list (defensive — would
 *     otherwise NaN-format and crash the metric strip).
 *  6. formatAvgDepth rounds the average half-up.
 */
class OpeningsActivityTest {

    @Test
    fun `DEFAULT_REPERTOIRE has exactly one active line`() {
        val active = OpeningsActivity.DEFAULT_REPERTOIRE.filter { it.isActive }
        assertEquals(
            "exactly one opening must be marked active so the design's " +
                "header + drill-button copy makes sense",
            1, active.size,
        )
    }

    @Test
    fun `DEFAULT_REPERTOIRE matches the four design ECO codes`() {
        val ecos = OpeningsActivity.DEFAULT_REPERTOIRE.map { it.eco }
        assertEquals(listOf("C84", "B22", "D02", "A04"), ecos)
    }

    @Test
    fun `DEFAULT_REPERTOIRE active line is the Ruy Lopez Closed`() {
        val active = OpeningsActivity.DEFAULT_REPERTOIRE.first { it.isActive }
        assertEquals("C84", active.eco)
        assertTrue(
            "active line's name must contain Ruy Lopez, got: ${active.name}",
            active.name.contains("Ruy Lopez"),
        )
    }

    @Test
    fun `DEFAULT_REPERTOIRE entries have UCI lines and mastery in range`() {
        for (entry in OpeningsActivity.DEFAULT_REPERTOIRE) {
            assertNotNull(entry.line)
            assertTrue("line for ${entry.eco} must be non-empty", entry.line.isNotBlank())
            assertTrue(
                "mastery for ${entry.eco} must be in [0, 1], got ${entry.mastery}",
                entry.mastery in 0f..1f,
            )
        }
    }

    @Test
    fun `formatMastery clamps below 0 to 0 percent`() {
        assertEquals("0%", OpeningsActivity.formatMastery(-0.1f))
        assertEquals("0%", OpeningsActivity.formatMastery(-1f))
    }

    @Test
    fun `formatMastery clamps above 1 to 100 percent`() {
        assertEquals("100%", OpeningsActivity.formatMastery(1.0f))
        assertEquals("100%", OpeningsActivity.formatMastery(1.5f))
        assertEquals("100%", OpeningsActivity.formatMastery(99f))
    }

    @Test
    fun `formatMastery rounds in-range values half-up`() {
        assertEquals("0%",   OpeningsActivity.formatMastery(0.001f))
        assertEquals("18%",  OpeningsActivity.formatMastery(0.18f))
        assertEquals("78%",  OpeningsActivity.formatMastery(0.78f))
        // 0.555 → 55.5% which rounds half-up to 56% via roundToInt
        // (Banker's rounding wouldn't apply since Float.roundToInt
        // uses HALF_UP semantics, not HALF_EVEN).
        assertEquals("56%",  OpeningsActivity.formatMastery(0.555f))
    }

    @Test
    fun `formatAvgDepth returns 0 for an empty list`() {
        assertEquals("0", OpeningsActivity.formatAvgDepth(emptyList()))
    }

    @Test
    fun `formatAvgDepth counts space-separated tokens per line`() {
        // Line "1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6" → 6 tokens
        val singleEntry = listOf(
            OpeningsActivity.OpeningEntry(
                eco = "C84",
                name = "Ruy Lopez · Closed",
                line = "1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6",
                mastery = 0.78f,
                isActive = true,
            ),
        )
        assertEquals("6", OpeningsActivity.formatAvgDepth(singleEntry))
    }

    @Test
    fun `formatAvgDepth rounds the average across multiple lines`() {
        val entries = listOf(
            OpeningsActivity.OpeningEntry("X", "x", "1 2 3", 0.5f, true),
            OpeningsActivity.OpeningEntry("Y", "y", "1 2 3 4", 0.5f, false),
        )
        // (3 + 4) / 2 = 3.5 → rounds half-up to 4
        assertEquals("4", OpeningsActivity.formatAvgDepth(entries))
    }
}
