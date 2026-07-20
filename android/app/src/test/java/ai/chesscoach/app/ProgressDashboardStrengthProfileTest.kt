package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [ProgressDashboardBottomSheet.strengthEntries] — the pure
 * transform behind the "Strongest sides" panel (2026-07-19).
 *
 * The panel used to show per-category WEAKNESS rates directly; it now shows
 * their inverse (strength = 1 − weakness), strongest-first, in the
 * positive/cyan signal.  These pins guard that inversion, the ordering, the
 * label mapping, and the [0, 1] clamp.
 *
 * Invariants pinned
 * -----------------
 *  STRENGTH_IS_INVERSE_OF_WEAKNESS  strength(cat) == 1 − weakness(cat).
 *  STRONGEST_FIRST                  entries ordered by descending strength
 *                                   (== ascending weakness).
 *  LABELS_MAPPED                    world-model keys map to display labels.
 *  UNKNOWN_KEY_PASSES_THROUGH       an unmapped key surfaces as its raw key.
 *  STRENGTH_CLAMPED                 out-of-range weakness clamps strength to [0, 1].
 *  EMPTY_IN_EMPTY_OUT               no categories → no bars.
 */
class ProgressDashboardStrengthProfileTest {

    private val delta = 1e-4f

    @Test
    fun `STRENGTH_IS_INVERSE_OF_WEAKNESS - strength is one minus weakness`() {
        val out = ProgressDashboardBottomSheet.strengthEntries(mapOf("tactical_vision" to 0.07f))
        assertEquals(1, out.size)
        assertEquals("Tactics", out[0].label)
        assertEquals(0.93f, out[0].strength, delta)
    }

    @Test
    fun `STRONGEST_FIRST - ordered by descending strength`() {
        val out = ProgressDashboardBottomSheet.strengthEntries(
            mapOf(
                "opening_preparation" to 0.40f, // strength 0.60 (weakest side)
                "tactical_vision" to 0.05f,     // strength 0.95 (strongest side)
                "positional_play" to 0.20f,     // strength 0.80
                "endgame_technique" to 0.10f,   // strength 0.90
            ),
        )
        assertEquals(listOf("Tactics", "Endgame", "Position", "Opening"), out.map { it.label })
        assertEquals(0.95f, out.first().strength, delta)
        assertEquals(0.60f, out.last().strength, delta)
        for (i in 1 until out.size) {
            assertTrue(
                "not strongest-first at $i: ${out.map { it.strength }}",
                out[i - 1].strength >= out[i].strength,
            )
        }
    }

    @Test
    fun `LABELS_MAPPED - world-model keys map to display labels`() {
        val labels = ProgressDashboardBottomSheet.strengthEntries(
            mapOf(
                "tactical_vision" to 0.1f,
                "opening_preparation" to 0.1f,
                "endgame_technique" to 0.1f,
                "positional_play" to 0.1f,
            ),
        ).map { it.label }.toSet()
        assertEquals(setOf("Tactics", "Opening", "Endgame", "Position"), labels)
    }

    @Test
    fun `UNKNOWN_KEY_PASSES_THROUGH - unmapped category surfaces as its raw key`() {
        val out = ProgressDashboardBottomSheet.strengthEntries(mapOf("king_safety" to 0.2f))
        assertEquals("king_safety", out[0].label)
        assertEquals(0.80f, out[0].strength, delta)
    }

    @Test
    fun `STRENGTH_CLAMPED - out-of-range weakness clamps strength into 0 to 1`() {
        val out = ProgressDashboardBottomSheet.strengthEntries(
            mapOf("a" to 1.5f, "b" to -0.5f),
        ).associate { it.label to it.strength }
        assertEquals(0f, out.getValue("a"), delta) // 1 - 1.5  = -0.5 → clamp 0
        assertEquals(1f, out.getValue("b"), delta) // 1 - -0.5 =  1.5 → clamp 1
    }

    @Test
    fun `EMPTY_IN_EMPTY_OUT - no categories yields no bars`() {
        assertTrue(ProgressDashboardBottomSheet.strengthEntries(emptyMap()).isEmpty())
    }
}
