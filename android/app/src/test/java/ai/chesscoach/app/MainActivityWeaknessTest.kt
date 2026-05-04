package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [MainActivity.Companion.computeWeaknesses].
 *
 * The function is pure arithmetic — no Android framework required.
 *
 * Invariants pinned
 * -----------------
 *  1.  WEAKNESS_EMPTY_INPUT:         empty list → emptyMap (no division by zero).
 *  2.  WEAKNESS_ALL_GOOD:            all GOOD moves → all rates are 0.
 *  3.  WEAKNESS_SINGLE_BLUNDER:      one blunder in one move → blunder_rate = 1.0.
 *  4.  WEAKNESS_RATES_SUM_LE_ONE:    blunder + mistake + inaccuracy ≤ 1.0 for any input.
 *  5.  WEAKNESS_MIXED:               mixed list produces correct per-category fractions.
 *  6.  WEAKNESS_KEYS_PRESENT:        returned map always has the three required keys.
 *  7.  WEAKNESS_BLUNDER_RATE_VALUE:  blunder_rate computed correctly from known input.
 *  8.  WEAKNESS_MISTAKE_RATE_VALUE:  mistake_rate computed correctly from known input.
 *  9.  WEAKNESS_INACCURACY_RATE:     inaccuracy_rate computed correctly from known input.
 * 10.  WEAKNESS_LARGE_GAME:          50 moves, 10 blunders → blunder_rate = 0.2.
 */
class MainActivityWeaknessTest {

    private val δ = 0.001f  // float comparison tolerance

    // ─────────────────────────────────────────────────────────────────────────
    // 1  Empty input
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_EMPTY_INPUT - empty list returns emptyMap`() {
        val result = MainActivity.computeWeaknesses(emptyList())
        assertTrue("Expected empty map, got: $result", result.isEmpty())
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2  All-good game
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_ALL_GOOD - all GOOD moves produce zero rates`() {
        val result = MainActivity.computeWeaknesses(
            List(10) { MistakeClassification.GOOD }
        )
        assertEquals(0f, result["blunder_rate"]!!, δ)
        assertEquals(0f, result["mistake_rate"]!!, δ)
        assertEquals(0f, result["inaccuracy_rate"]!!, δ)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3  Single blunder
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_SINGLE_BLUNDER - one blunder equals blunder_rate of 1_0`() {
        val result = MainActivity.computeWeaknesses(
            listOf(MistakeClassification.BLUNDER)
        )
        assertEquals(1f, result["blunder_rate"]!!, δ)
        assertEquals(0f, result["mistake_rate"]!!, δ)
        assertEquals(0f, result["inaccuracy_rate"]!!, δ)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4  Rates sum ≤ 1
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_RATES_SUM_LE_ONE - sum of rates never exceeds 1`() {
        val classifications = listOf(
            MistakeClassification.BLUNDER,
            MistakeClassification.MISTAKE,
            MistakeClassification.INACCURACY,
            MistakeClassification.GOOD,
        )
        val result = MainActivity.computeWeaknesses(classifications)
        val sum = (result["blunder_rate"] ?: 0f) +
                  (result["mistake_rate"] ?: 0f) +
                  (result["inaccuracy_rate"] ?: 0f)
        assertTrue("Sum of rates ($sum) must be ≤ 1.0", sum <= 1.0f + δ)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 5  Mixed input
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_MIXED - mixed list produces correct per-category fractions`() {
        // 2 blunders, 1 mistake, 1 inaccuracy, 6 good → 10 total
        val classifications = List(2) { MistakeClassification.BLUNDER } +
                              List(1) { MistakeClassification.MISTAKE } +
                              List(1) { MistakeClassification.INACCURACY } +
                              List(6) { MistakeClassification.GOOD }
        val result = MainActivity.computeWeaknesses(classifications)
        assertEquals(0.2f, result["blunder_rate"]!!,    δ)
        assertEquals(0.1f, result["mistake_rate"]!!,    δ)
        assertEquals(0.1f, result["inaccuracy_rate"]!!, δ)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 6  Required keys always present
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_KEYS_PRESENT - non-empty result always contains all three keys`() {
        val result = MainActivity.computeWeaknesses(
            listOf(MistakeClassification.GOOD)
        )
        assertTrue("blunder_rate key missing",    "blunder_rate"    in result)
        assertTrue("mistake_rate key missing",    "mistake_rate"    in result)
        assertTrue("inaccuracy_rate key missing", "inaccuracy_rate" in result)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 7–9  Individual rate computations
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_BLUNDER_RATE_VALUE - blunder_rate is blunders divided by total`() {
        // 3 blunders out of 12 → 0.25
        val input = List(3) { MistakeClassification.BLUNDER } +
                    List(9) { MistakeClassification.GOOD }
        assertEquals(0.25f, MainActivity.computeWeaknesses(input)["blunder_rate"]!!, δ)
    }

    @Test
    fun `WEAKNESS_MISTAKE_RATE_VALUE - mistake_rate is mistakes divided by total`() {
        // 4 mistakes out of 8 → 0.5
        val input = List(4) { MistakeClassification.MISTAKE } +
                    List(4) { MistakeClassification.GOOD }
        assertEquals(0.5f, MainActivity.computeWeaknesses(input)["mistake_rate"]!!, δ)
    }

    @Test
    fun `WEAKNESS_INACCURACY_RATE - inaccuracy_rate is inaccuracies divided by total`() {
        // 1 inaccuracy out of 4 → 0.25
        val input = List(1) { MistakeClassification.INACCURACY } +
                    List(3) { MistakeClassification.GOOD }
        assertEquals(0.25f, MainActivity.computeWeaknesses(input)["inaccuracy_rate"]!!, δ)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 10  Large game
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `WEAKNESS_LARGE_GAME - 50 moves with 10 blunders gives blunder_rate 0_2`() {
        val input = List(10) { MistakeClassification.BLUNDER } +
                    List(40) { MistakeClassification.GOOD }
        assertEquals(0.2f, MainActivity.computeWeaknesses(input)["blunder_rate"]!!, δ)
    }
}
