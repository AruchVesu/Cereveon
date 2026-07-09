package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the [ProgressDashboardBottomSheet.Companion] pure
 * helpers behind the profile's human-progress header ("You" surface).
 *
 * Invariants pinned
 * -----------------
 *  HERO_LEVEL_BOUNDARIES    formatHeroLevel walks the 100-XP linear curve:
 *                           0 and 99 XP → Level 1, 100 XP → Level 2.
 *  HERO_LEVEL_NEGATIVE      negative XP (corrupt cache) clamps to Level 1.
 *  HERO_LEVEL_MATCHES_HOME  the hero level always equals the level the Home
 *                           kicker renders for the same XP — the two surfaces
 *                           share [HomeActivity.XP_PER_LEVEL] and must never
 *                           disagree on the level curve.
 *  HERO_XP_FORMAT           formatHeroXp renders "<xp> XP", clamping
 *                           negatives to 0.
 *  GAMES_SUMMARY_EMPTY      empty history → "0 played · 0 won".
 *  GAMES_SUMMARY_MIXED      wins counted, losses/draws only add to played.
 *  GAMES_SUMMARY_CASE       result matching is case-insensitive ("WIN").
 */
class ProgressDashboardHeroHeaderTest {

    private fun game(result: String) = ProgressHistoryItem(result = result)

    // ─────────────────────────────────────────────────────────────────────────
    // formatHeroLevel
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `HERO_LEVEL_BOUNDARIES - 100 XP linear curve`() {
        assertEquals("Level 1", ProgressDashboardBottomSheet.formatHeroLevel(0))
        assertEquals("Level 1", ProgressDashboardBottomSheet.formatHeroLevel(99))
        assertEquals("Level 2", ProgressDashboardBottomSheet.formatHeroLevel(100))
        assertEquals("Level 4", ProgressDashboardBottomSheet.formatHeroLevel(340))
    }

    @Test
    fun `HERO_LEVEL_NEGATIVE - corrupt cache clamps to Level 1`() {
        assertEquals("Level 1", ProgressDashboardBottomSheet.formatHeroLevel(-25))
    }

    @Test
    fun `HERO_LEVEL_MATCHES_HOME - profile hero and Home kicker agree on the level`() {
        // formatXpKicker renders "Level N · X XP"; the hero renders
        // "Level N".  Pin agreement across the curve so a future change
        // to either formula breaks a test instead of shipping two
        // surfaces that disagree about the player's level.
        for (xp in listOf(0, 1, 99, 100, 101, 340, 999, 1000)) {
            val homeLevel = HomeActivity.formatXpKicker(xp).substringBefore(" ·")
            assertEquals(
                "Level mismatch between Home kicker and profile hero at $xp XP",
                homeLevel,
                ProgressDashboardBottomSheet.formatHeroLevel(xp),
            )
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // formatHeroXp
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `HERO_XP_FORMAT - renders raw XP with unit, clamping negatives`() {
        assertEquals("0 XP", ProgressDashboardBottomSheet.formatHeroXp(0))
        assertEquals("340 XP", ProgressDashboardBottomSheet.formatHeroXp(340))
        assertEquals("0 XP", ProgressDashboardBottomSheet.formatHeroXp(-25))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // formatGamesSummary
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `GAMES_SUMMARY_EMPTY - fresh account reads zero played zero won`() {
        assertEquals(
            "0 played · 0 won",
            ProgressDashboardBottomSheet.formatGamesSummary(emptyList()),
        )
    }

    @Test
    fun `GAMES_SUMMARY_MIXED - draws and losses add to played but never to won`() {
        val history = listOf(
            game("win"), game("loss"), game("draw"), game("win"), game("loss"),
        )
        assertEquals(
            "5 played · 2 won",
            ProgressDashboardBottomSheet.formatGamesSummary(history),
        )
    }

    @Test
    fun `GAMES_SUMMARY_CASE - result matching is case-insensitive`() {
        val history = listOf(game("WIN"), game("Win"), game("draw"))
        val summary = ProgressDashboardBottomSheet.formatGamesSummary(history)
        assertTrue("Expected 2 won in: $summary", summary.endsWith("2 won"))
        assertEquals("3 played · 2 won", summary)
    }
}
