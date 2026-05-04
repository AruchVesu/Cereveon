package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pure-Kotlin unit tests for the static helpers on
 * [OnboardingActivity.Companion].  These are exercised on the host JVM
 * without instrumentation since the helpers do not touch the Android
 * framework.
 *
 * Invariants pinned
 * -----------------
 *  1. formatRating rounds half-up to a bare integer string.
 *  2. formatFirstOpponent biases ~40 below the player's estimate
 *     (per the handoff) and floors at 800.
 *  3. confidenceFromKey maps known keys to documented values and
 *     falls back to the "guessing" weight (0.5) for anything else.
 *  4. The weights are strictly ordered "sure" > "guessing" > "rusty"
 *     so the adaptation layer can use them as an uncertainty band.
 */
class OnboardingActivityTest {

    @Test
    fun `formatRating renders the rounded integer rating`() {
        assertEquals("1500", OnboardingActivity.formatRating(1500f))
        assertEquals("1720", OnboardingActivity.formatRating(1720.4f))
        assertEquals("1721", OnboardingActivity.formatRating(1720.6f))
    }

    @Test
    fun `formatFirstOpponent biases 40 below the slider value`() {
        assertEquals("~1460 · adaptive", OnboardingActivity.formatFirstOpponent(1500f))
        assertEquals("~1680 · adaptive", OnboardingActivity.formatFirstOpponent(1720f))
    }

    @Test
    fun `formatFirstOpponent floors at 800 for very low ratings`() {
        // Slider min is 800, so player - 40 would dip below 800; the
        // first opponent is clamped so the engine never receives a
        // sub-800 target.
        assertEquals("~800 · adaptive", OnboardingActivity.formatFirstOpponent(800f))
        assertEquals("~800 · adaptive", OnboardingActivity.formatFirstOpponent(820f))
    }

    @Test
    fun `confidenceFromKey returns the documented weights`() {
        assertEquals(0.85f, OnboardingActivity.confidenceFromKey("sure"), 1e-6f)
        assertEquals(0.50f, OnboardingActivity.confidenceFromKey("guessing"), 1e-6f)
        assertEquals(0.25f, OnboardingActivity.confidenceFromKey("rusty"), 1e-6f)
    }

    @Test
    fun `confidenceFromKey is case-insensitive`() {
        assertEquals(0.85f, OnboardingActivity.confidenceFromKey("SURE"), 1e-6f)
        assertEquals(0.25f, OnboardingActivity.confidenceFromKey("Rusty"), 1e-6f)
    }

    @Test
    fun `confidenceFromKey falls back to the guessing weight for unknown keys`() {
        assertEquals(0.50f, OnboardingActivity.confidenceFromKey(""), 1e-6f)
        assertEquals(0.50f, OnboardingActivity.confidenceFromKey("definitely"), 1e-6f)
    }

    @Test
    fun `confidence weights are strictly ordered`() {
        val sure = OnboardingActivity.confidenceFromKey("sure")
        val guessing = OnboardingActivity.confidenceFromKey("guessing")
        val rusty = OnboardingActivity.confidenceFromKey("rusty")
        assert(sure > guessing) { "sure ($sure) must be greater than guessing ($guessing)" }
        assert(guessing > rusty) { "guessing ($guessing) must be greater than rusty ($rusty)" }
    }

    @Test
    fun `default rating matches the slider's neutral midpoint`() {
        // The slider goes from 800 to 2600; 1500 is the canonical
        // "I have no idea" anchor used elsewhere in the app
        // (MainActivity initial rating cache, etc.).
        assertEquals(1500f, OnboardingActivity.DEFAULT_RATING)
    }

    @Test
    fun `default confidence is the middle bucket`() {
        assertEquals("guessing", OnboardingActivity.DEFAULT_CONFIDENCE)
        assertEquals(
            0.50f,
            OnboardingActivity.confidenceFromKey(OnboardingActivity.DEFAULT_CONFIDENCE),
            1e-6f,
        )
    }
}
