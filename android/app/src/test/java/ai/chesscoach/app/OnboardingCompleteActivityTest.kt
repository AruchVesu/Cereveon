package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pure-Kotlin tests for [OnboardingCompleteActivity.formatConfidenceLabel].
 *
 * Invariants pinned
 * -----------------
 *  1. Each canonical confidence value (0.85 / 0.50 / 0.25) maps back
 *     to the same human-readable label the calibration screen showed.
 *  2. Float round-trip noise from SharedPreferences write/read can't
 *     bucket a clear "Sure of it" choice into "Guessing" — buckets
 *     have a ±0.05 cushion around the canonical values.
 *  3. Out-of-range / corrupted values fall through to the safest
 *     bucket ("Rusty") rather than throwing.
 */
class OnboardingCompleteActivityTest {

    @Test
    fun `0_85 maps to Sure of it`() {
        assertEquals(
            "Sure of it",
            OnboardingCompleteActivity.formatConfidenceLabel(0.85f),
        )
    }

    @Test
    fun `0_50 maps to Guessing`() {
        assertEquals(
            "Guessing",
            OnboardingCompleteActivity.formatConfidenceLabel(0.50f),
        )
    }

    @Test
    fun `0_25 maps to Rusty`() {
        assertEquals(
            "Rusty",
            OnboardingCompleteActivity.formatConfidenceLabel(0.25f),
        )
    }

    @Test
    fun `slight float noise around canonical values keeps the same bucket`() {
        // SharedPreferences float writes/reads sometimes lose a few
        // ULPs; a "Sure of it" choice (0.85) must NOT degrade to
        // "Guessing" if the value comes back as 0.8499999.
        assertEquals(
            "Sure of it",
            OnboardingCompleteActivity.formatConfidenceLabel(0.8499999f),
        )
        assertEquals(
            "Sure of it",
            OnboardingCompleteActivity.formatConfidenceLabel(0.8500001f),
        )
        assertEquals(
            "Guessing",
            OnboardingCompleteActivity.formatConfidenceLabel(0.4999999f),
        )
        assertEquals(
            "Rusty",
            OnboardingCompleteActivity.formatConfidenceLabel(0.2500001f),
        )
    }

    @Test
    fun `bucket boundaries match the documented thresholds`() {
        // ≥ 0.70 → Sure of it, ≥ 0.40 → Guessing, < 0.40 → Rusty
        assertEquals("Sure of it", OnboardingCompleteActivity.formatConfidenceLabel(0.70f))
        assertEquals("Guessing",   OnboardingCompleteActivity.formatConfidenceLabel(0.40f))
        assertEquals("Guessing",   OnboardingCompleteActivity.formatConfidenceLabel(0.69f))
        assertEquals("Rusty",      OnboardingCompleteActivity.formatConfidenceLabel(0.39f))
    }

    @Test
    fun `out of range values fall through to safe buckets`() {
        // Defensive: a corrupt prefs value mustn't throw; it lands in
        // the lowest bucket ("Rusty") for negatives and the highest
        // ("Sure of it") for >1.
        assertEquals("Rusty",      OnboardingCompleteActivity.formatConfidenceLabel(-0.5f))
        assertEquals("Sure of it", OnboardingCompleteActivity.formatConfidenceLabel(2.0f))
    }
}
