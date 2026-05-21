package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pure-Kotlin tests for the static helpers on
 * [TodaysDrillBottomSheet.Companion].  The fragment itself extends
 * [androidx.fragment.app.DialogFragment] and can't run on the host
 * JVM without Robolectric — these tests cover the formatting +
 * source_ref shape that drives /training/solve dedup, without
 * standing up an Activity.
 *
 * Invariants pinned
 * -----------------
 *  1. SOURCE_REF_DAY0           plan_<id>:day_0 — first puzzle in the plan.
 *  2. SOURCE_REF_DAY3           plan_<id>:day_3 — spaced-repetition day 3.
 *  3. SOURCE_REF_DAY7           plan_<id>:day_7 — spaced-repetition day 7.
 *  4. KICKER_DAY0_NAMED_THEME   "Day 1 of 3 · King safety" — day-0 displays as "Day 1".
 *  5. KICKER_DAY3_NAMED_THEME   "Day 2 of 3 · Fork".
 *  6. KICKER_DAY7_NAMED_THEME   "Day 3 of 3 · Back rank".
 *  7. KICKER_GENERIC_THEME      "Day 1 of 3" — no themed segment when theme is "generic".
 *  8. KICKER_EMPTY_THEME        "Day 1 of 3" — no themed segment when theme is empty.
 *  9. KICKER_UNKNOWN_DAY        Fall through to "Day 1" for any out-of-vocab day_offset.
 * 10. PRETTY_THEME_GENERIC      "generic" → empty string (signal: hide theme segment).
 * 11. PRETTY_THEME_SNAKE_CASE   "king_safety" → "King safety" (sentence-cased).
 * 12. PRETTY_THEME_SINGLE_WORD  "fork" → "Fork".
 * 13. PRETTY_THEME_EMPTY        "" → empty string.
 */
class TodaysDrillBottomSheetTest {

    // ── formatSourceRef ──────────────────────────────────────────────

    @Test
    fun `SOURCE_REF_DAY0 - day-0 source_ref shape`() {
        assertEquals(
            "plan_9e5f9966:day_0",
            TodaysDrillBottomSheet.formatSourceRef(planId = "9e5f9966", dayOffset = 0),
        )
    }

    @Test
    fun `SOURCE_REF_DAY3 - day-3 source_ref shape`() {
        assertEquals(
            "plan_abc:day_3",
            TodaysDrillBottomSheet.formatSourceRef(planId = "abc", dayOffset = 3),
        )
    }

    @Test
    fun `SOURCE_REF_DAY7 - day-7 source_ref shape`() {
        assertEquals(
            "plan_xyz:day_7",
            TodaysDrillBottomSheet.formatSourceRef(planId = "xyz", dayOffset = 7),
        )
    }

    // ── formatKicker ─────────────────────────────────────────────────

    @Test
    fun `KICKER_DAY0_NAMED_THEME - day-0 displays as Day 1`() {
        assertEquals(
            "Day 1 of 3 · King safety",
            TodaysDrillBottomSheet.formatKicker(
                dayOffset = 0, totalDays = 3, theme = "king_safety",
            ),
        )
    }

    @Test
    fun `KICKER_DAY3_NAMED_THEME - day-3 displays as Day 2`() {
        assertEquals(
            "Day 2 of 3 · Fork",
            TodaysDrillBottomSheet.formatKicker(
                dayOffset = 3, totalDays = 3, theme = "fork",
            ),
        )
    }

    @Test
    fun `KICKER_DAY7_NAMED_THEME - day-7 displays as Day 3`() {
        assertEquals(
            "Day 3 of 3 · Back rank",
            TodaysDrillBottomSheet.formatKicker(
                dayOffset = 7, totalDays = 3, theme = "back_rank",
            ),
        )
    }

    @Test
    fun `KICKER_GENERIC_THEME - generic theme drops the themed segment`() {
        assertEquals(
            "Day 1 of 3",
            TodaysDrillBottomSheet.formatKicker(
                dayOffset = 0, totalDays = 3, theme = "generic",
            ),
        )
    }

    @Test
    fun `KICKER_EMPTY_THEME - empty theme drops the themed segment`() {
        assertEquals(
            "Day 1 of 3",
            TodaysDrillBottomSheet.formatKicker(
                dayOffset = 0, totalDays = 3, theme = "",
            ),
        )
    }

    @Test
    fun `KICKER_UNKNOWN_DAY - unknown day_offset falls back to Day 1`() {
        // Defensive: any out-of-vocab day_offset (5, 100, -1)
        // should display as "Day 1" rather than crash.
        assertEquals(
            "Day 1 of 3 · Fork",
            TodaysDrillBottomSheet.formatKicker(
                dayOffset = 99, totalDays = 3, theme = "fork",
            ),
        )
    }

    // ── prettyTheme ──────────────────────────────────────────────────

    @Test
    fun `PRETTY_THEME_GENERIC - generic collapses to empty string`() {
        assertEquals("", TodaysDrillBottomSheet.prettyTheme("generic"))
    }

    @Test
    fun `PRETTY_THEME_EMPTY - empty input collapses to empty string`() {
        assertEquals("", TodaysDrillBottomSheet.prettyTheme(""))
        assertEquals("", TodaysDrillBottomSheet.prettyTheme("   "))
    }

    @Test
    fun `PRETTY_THEME_SNAKE_CASE - snake_case becomes sentence case`() {
        assertEquals("King safety", TodaysDrillBottomSheet.prettyTheme("king_safety"))
        assertEquals("Back rank", TodaysDrillBottomSheet.prettyTheme("back_rank"))
        assertEquals("Hung piece", TodaysDrillBottomSheet.prettyTheme("hung_piece"))
        assertEquals(
            "Endgame technique",
            TodaysDrillBottomSheet.prettyTheme("endgame_technique"),
        )
        assertEquals(
            "Opening principles",
            TodaysDrillBottomSheet.prettyTheme("opening_principles"),
        )
    }

    @Test
    fun `PRETTY_THEME_SINGLE_WORD - single word capitalises only the first letter`() {
        assertEquals("Fork", TodaysDrillBottomSheet.prettyTheme("fork"))
        assertEquals("Pin", TodaysDrillBottomSheet.prettyTheme("pin"))
        assertEquals("Tempo", TodaysDrillBottomSheet.prettyTheme("tempo"))
    }
}
