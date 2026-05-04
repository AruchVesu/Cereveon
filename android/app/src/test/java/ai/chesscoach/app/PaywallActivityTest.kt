package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-Kotlin tests for the static helpers + canonical defaults on
 * [PaywallActivity.Companion].  Run on the host JVM without
 * instrumentation since the helpers don't touch the Android framework.
 *
 * Invariants pinned
 * -----------------
 *  1. DEFAULT_PLANS contains exactly one recommended entry.
 *  2. DEFAULT_PLANS keys match the activity's hard-coded selection
 *     keys ("monthly" / "yearly") so the tap → selectPlan() path
 *     can never miss.
 *  3. DEFAULT_FEATURES has 4 entries (the design's bullet count).
 *  4. recommendedPlanKey returns "yearly" by default so the activity's
 *     initial active-tile state matches the design.
 *  5. recommendedPlanKey falls back to the first plan when no entry
 *     is marked recommended (defensive — a misconfigured rollout
 *     mustn't render the activity with no active tile).
 *  6. recommendedPlanKey falls back to "yearly" string literal when
 *     the list is empty (extreme edge — keeps the call infallible).
 */
class PaywallActivityTest {

    @Test
    fun `DEFAULT_PLANS has exactly one recommended entry`() {
        val recommended = PaywallActivity.DEFAULT_PLANS.filter { it.isRecommended }
        assertEquals(
            "exactly one plan must be marked recommended so the initial " +
                "active tile is unambiguous",
            1, recommended.size,
        )
    }

    @Test
    fun `DEFAULT_PLANS keys match the activity's tap selection keys`() {
        val keys = PaywallActivity.DEFAULT_PLANS.map { it.key }.toSet()
        // selectPlan("monthly") and selectPlan("yearly") are the only
        // values the click listeners pass; if these diverge the activity
        // silently does nothing on tap.
        assertEquals(setOf("monthly", "yearly"), keys)
    }

    @Test
    fun `DEFAULT_PLANS recommended is yearly`() {
        val recommended = PaywallActivity.DEFAULT_PLANS.first { it.isRecommended }
        assertEquals("yearly", recommended.key)
        assertEquals("Yearly", recommended.title)
    }

    @Test
    fun `DEFAULT_PLANS entries have non-blank prices and subs`() {
        for (plan in PaywallActivity.DEFAULT_PLANS) {
            assertNotNull(plan.price)
            assertNotNull(plan.sub)
            assertTrue("plan ${plan.key} price must be non-blank", plan.price.isNotBlank())
            assertTrue("plan ${plan.key} sub must be non-blank",   plan.sub.isNotBlank())
        }
    }

    @Test
    fun `DEFAULT_FEATURES has the design's four bullets`() {
        assertEquals(4, PaywallActivity.DEFAULT_FEATURES.size)
        for (bullet in PaywallActivity.DEFAULT_FEATURES) {
            assertTrue("feature bullet must be non-blank", bullet.isNotBlank())
        }
    }

    @Test
    fun `recommendedPlanKey returns yearly by default`() {
        assertEquals("yearly", PaywallActivity.recommendedPlanKey())
    }

    @Test
    fun `recommendedPlanKey falls back to first plan when none recommended`() {
        // Defensive fallback — a misconfigured rollout (no recommended
        // flag set anywhere) shouldn't strand the activity with no
        // active tile.  First plan in the list wins.
        val plans = listOf(
            PaywallActivity.Plan("a", "A", "$1", "x", isRecommended = false),
            PaywallActivity.Plan("b", "B", "$2", "y", isRecommended = false),
        )
        assertEquals("a", PaywallActivity.recommendedPlanKey(plans))
    }

    @Test
    fun `recommendedPlanKey falls back to yearly literal for empty list`() {
        // Extreme edge — a backend that returns an empty plan catalog
        // (network timeout, A/B test misfire) shouldn't crash the
        // initial selectPlan() call.
        assertEquals("yearly", PaywallActivity.recommendedPlanKey(emptyList()))
    }
}
