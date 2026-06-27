package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Host-JVM tests for [StudyPlanOverviewBottomSheet]'s pure rendering
 * helpers — the focus title, day labels/status, progress, and CTA copy
 * that drive the week-overview screen.  No Robolectric: these cover the
 * formatting logic only; layout inflation is covered by the instrumented
 * [AtriumLayoutInflationTest], and the fragment's view wiring by the
 * same XML IDs.
 *
 * Pinned invariants
 * -----------------
 *  FOCUS_*       formatFocus prefers the aggregate category, then the
 *                day-0 theme, then a neutral default.
 *  CATEGORY_*    formatCategory maps the four MistakeCategory values to
 *                friendly nouns; unknown/null → "".
 *  DAY_NUMBER_*  dayNumber maps offsets 0/3/7 → 1/2/3.
 *  DAY_LABEL_*   formatDayLabel distinguishes the day-0 replay from
 *                library practice.
 *  STATUS_*      statusText maps completed/is_due/locked.
 *  PROGRESS_*    formatProgress counts completed days; all done →
 *                "Week complete".
 *  CTA_*         formatCtaLabel names the due day.
 */
class StudyPlanOverviewBottomSheetTest {

    private fun day(
        offset: Int,
        completed: Boolean = false,
        isDue: Boolean = false,
        source: String = "library",
    ) = PlanDayDto(
        dayOffset = offset,
        dueAt = "2026-06-27T00:00:00",
        completed = completed,
        isDue = isDue,
        sourceType = source,
    )

    // ── formatCategory ───────────────────────────────────────────────

    @Test
    fun `CATEGORY_KNOWN - the four categories map to friendly nouns`() {
        assertEquals("Tactics", StudyPlanOverviewBottomSheet.formatCategory("tactical_vision"))
        assertEquals("Endgames", StudyPlanOverviewBottomSheet.formatCategory("endgame_technique"))
        assertEquals("Openings", StudyPlanOverviewBottomSheet.formatCategory("opening_preparation"))
        assertEquals("Strategy", StudyPlanOverviewBottomSheet.formatCategory("positional_play"))
    }

    @Test
    fun `CATEGORY_UNKNOWN - null or unrecognised category maps to empty`() {
        assertEquals("", StudyPlanOverviewBottomSheet.formatCategory(null))
        assertEquals("", StudyPlanOverviewBottomSheet.formatCategory("generic"))
        assertEquals("", StudyPlanOverviewBottomSheet.formatCategory("nonsense"))
    }

    // ── formatFocus ──────────────────────────────────────────────────

    @Test
    fun `FOCUS_PREFERS_CATEGORY - anchor category wins over theme`() {
        val plan = CoachPlanResponse(anchorCategory = "tactical_vision", theme = "king_safety")
        assertEquals("Tactics", StudyPlanOverviewBottomSheet.formatFocus(plan))
    }

    @Test
    fun `FOCUS_FALLS_BACK_TO_THEME - no category, use the day-0 theme`() {
        val plan = CoachPlanResponse(anchorCategory = null, theme = "king_safety")
        assertEquals("King safety", StudyPlanOverviewBottomSheet.formatFocus(plan))
    }

    @Test
    fun `FOCUS_NEUTRAL_DEFAULT - no category and generic theme falls back to This week`() {
        val plan = CoachPlanResponse(anchorCategory = null, theme = "generic")
        assertEquals("This week", StudyPlanOverviewBottomSheet.formatFocus(plan))
    }

    // ── dayNumber ────────────────────────────────────────────────────

    @Test
    fun `DAY_NUMBER_MAPS_OFFSETS - 0 3 7 become 1 2 3`() {
        assertEquals(1, StudyPlanOverviewBottomSheet.dayNumber(0))
        assertEquals(2, StudyPlanOverviewBottomSheet.dayNumber(3))
        assertEquals(3, StudyPlanOverviewBottomSheet.dayNumber(7))
    }

    @Test
    fun `DAY_NUMBER_UNKNOWN_OFFSET - falls back to 1`() {
        assertEquals(1, StudyPlanOverviewBottomSheet.dayNumber(99))
    }

    // ── formatDayLabel ───────────────────────────────────────────────

    @Test
    fun `DAY_LABEL_ORIGINAL - day-0 reads as a mistake replay`() {
        assertEquals(
            "Day 1 · Replay your mistake",
            StudyPlanOverviewBottomSheet.formatDayLabel(1, "original"),
        )
    }

    @Test
    fun `DAY_LABEL_LIBRARY - library days read as practice`() {
        assertEquals(
            "Day 2 · Practice",
            StudyPlanOverviewBottomSheet.formatDayLabel(2, "library"),
        )
    }

    // ── statusText ───────────────────────────────────────────────────

    @Test
    fun `STATUS_DONE - completed day reads Done`() {
        assertEquals("Done", StudyPlanOverviewBottomSheet.statusText(day(0, completed = true)))
    }

    @Test
    fun `STATUS_TODAY - due-and-incomplete day reads Today`() {
        assertEquals("Today", StudyPlanOverviewBottomSheet.statusText(day(3, isDue = true)))
    }

    @Test
    fun `STATUS_LOCKED - not-due not-complete day reads Locked`() {
        assertEquals("Locked", StudyPlanOverviewBottomSheet.statusText(day(7)))
    }

    @Test
    fun `STATUS_COMPLETED_WINS - a completed day reads Done even if marked due`() {
        // Defensive: completed takes precedence over is_due.
        assertEquals(
            "Done",
            StudyPlanOverviewBottomSheet.statusText(day(0, completed = true, isDue = true)),
        )
    }

    // ── formatProgress ───────────────────────────────────────────────

    @Test
    fun `PROGRESS_FRESH - no days done is Day 1 of 3`() {
        val days = listOf(day(0, isDue = true), day(3), day(7))
        assertEquals("Day 1 of 3", StudyPlanOverviewBottomSheet.formatProgress(days, 3))
    }

    @Test
    fun `PROGRESS_MIDWAY - one day done is Day 2 of 3`() {
        val days = listOf(day(0, completed = true), day(3, isDue = true), day(7))
        assertEquals("Day 2 of 3", StudyPlanOverviewBottomSheet.formatProgress(days, 3))
    }

    @Test
    fun `PROGRESS_COMPLETE - all days done reads Week complete`() {
        val days = listOf(
            day(0, completed = true),
            day(3, completed = true),
            day(7, completed = true),
        )
        assertEquals("Week complete", StudyPlanOverviewBottomSheet.formatProgress(days, 3))
    }

    // ── formatCtaLabel ───────────────────────────────────────────────

    @Test
    fun `CTA_NAMES_DUE_DAY - label points at the due day`() {
        assertEquals("Start day 2", StudyPlanOverviewBottomSheet.formatCtaLabel(2))
    }
}
