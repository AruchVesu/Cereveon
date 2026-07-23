package com.cereveon.myapp

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
 * 14. WALKABLE_THRESHOLD        a line walks at >= 3 plies (two solver decisions);
 *                               shorter/empty lines run the single-decision drill.
 * 15. WALK_CONTINUES_ON_LINE    a line-following move mid-walk yields Continue with
 *                               the scripted reply + the next solver index.
 * 16. WALK_SOLVES_ON_LAST_MOVE  the line's final solver move yields Solved.
 * 17. WALK_SOLVES_ON_DEVIATION  an engine-approved move OFF the line yields Solved
 *                               (never punished, never walks a stale script).
 * 18. WALK_SOLVES_ON_TRAILING_REPLY  a (malformed) line ending on an opponent reply
 *                               solves at the boundary instead of stranding the walk.
 * 19. UCI_COORDS_ROUNDTRIP      uciToCoords inverts rowColToUci; malformed → null.
 * 20. UCI_PROMO_CHAR            5-char UCI exposes the promo letter, else ' '.
 * 21. WALK_STATUS_COPY          upfront depth announcement + per-step progress.
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

    // ── Multi-move walk state machine ────────────────────────────────

    // 1.e4 e5 2.Nf3-style stand-in line: solver at 0 and 2, reply at 1.
    private val threePlyLine = listOf("e2e4", "e7e5", "g1f3")
    private val fivePlyLine = listOf("e2e4", "e7e5", "g1f3", "b8c6", "f1c4")

    @Test
    fun `WALKABLE_THRESHOLD - three plies walk, fewer run single-decision`() {
        assertEquals(false, TodaysDrillBottomSheet.isWalkable(emptyList()))
        assertEquals(false, TodaysDrillBottomSheet.isWalkable(listOf("e2e4")))
        assertEquals(false, TodaysDrillBottomSheet.isWalkable(listOf("e2e4", "e7e5")))
        assertEquals(true, TodaysDrillBottomSheet.isWalkable(threePlyLine))
        assertEquals(true, TodaysDrillBottomSheet.isWalkable(fivePlyLine))
    }

    @Test
    fun `WALKABLE_THRESHOLD - solver move count is the even-index ply count`() {
        assertEquals(0, TodaysDrillBottomSheet.solverMoveCount(emptyList()))
        assertEquals(1, TodaysDrillBottomSheet.solverMoveCount(listOf("e2e4")))
        assertEquals(2, TodaysDrillBottomSheet.solverMoveCount(threePlyLine))
        assertEquals(3, TodaysDrillBottomSheet.solverMoveCount(fivePlyLine))
    }

    @Test
    fun `WALK_CONTINUES_ON_LINE - line-following move yields the scripted reply`() {
        val step = TodaysDrillBottomSheet.nextDrillStep(fivePlyLine, 0, "e2e4")
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Continue(
                opponentReplyUci = "e7e5",
                nextLineIndex = 2,
            ),
            step,
        )
        val second = TodaysDrillBottomSheet.nextDrillStep(fivePlyLine, 2, "g1f3")
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Continue(
                opponentReplyUci = "b8c6",
                nextLineIndex = 4,
            ),
            second,
        )
    }

    @Test
    fun `WALK_SOLVES_ON_LAST_MOVE - the final solver move completes the drill`() {
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Solved,
            TodaysDrillBottomSheet.nextDrillStep(threePlyLine, 2, "g1f3"),
        )
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Solved,
            TodaysDrillBottomSheet.nextDrillStep(fivePlyLine, 4, "f1c4"),
        )
    }

    @Test
    fun `WALK_SOLVES_ON_DEVIATION - an engine-approved off-line move completes`() {
        // The engine said the move is sound but it isn't the scripted one:
        // the rest of the line no longer applies, so the drill is solved.
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Solved,
            TodaysDrillBottomSheet.nextDrillStep(fivePlyLine, 0, "d2d4"),
        )
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Solved,
            TodaysDrillBottomSheet.nextDrillStep(fivePlyLine, 2, "b1c3"),
        )
    }

    @Test
    fun `WALK_SOLVES_ON_SINGLE_DECISION - short and empty lines always solve`() {
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Solved,
            TodaysDrillBottomSheet.nextDrillStep(emptyList(), 0, "e2e4"),
        )
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Solved,
            TodaysDrillBottomSheet.nextDrillStep(listOf("e2e4"), 0, "e2e4"),
        )
    }

    @Test
    fun `WALK_SOLVES_ON_TRAILING_REPLY - a line ending on a reply solves at the boundary`() {
        // Defensive: a 4-ply line ends on an opponent reply.  The move at
        // index 2 is the last SOLVER move — there is no next decision, so
        // the walk must solve rather than script a trailing reply with
        // nothing to find after it.
        val evenLine = listOf("e2e4", "e7e5", "g1f3", "b8c6")
        assertEquals(
            TodaysDrillBottomSheet.DrillStepOutcome.Solved,
            TodaysDrillBottomSheet.nextDrillStep(evenLine, 2, "g1f3"),
        )
    }

    // ── UCI coordinate helpers ───────────────────────────────────────

    @Test
    fun `UCI_COORDS_ROUNDTRIP - uciToCoords inverts rowColToUci`() {
        // e2 = row 6, col 4; e4 = row 4, col 4 in ChessBoardView's frame.
        val coords = TodaysDrillBottomSheet.uciToCoords("e2e4")!!
        assertEquals(listOf(6, 4, 4, 4), coords.toList())
        assertEquals(
            "e2",
            MistakeReplayBottomSheet.rowColToUci(coords[0], coords[1]),
        )
        assertEquals(
            "e4",
            MistakeReplayBottomSheet.rowColToUci(coords[2], coords[3]),
        )
        // Corner-to-corner sanity: a8 is row 0 col 0, h1 is row 7 col 7.
        assertEquals(
            listOf(0, 0, 7, 7),
            TodaysDrillBottomSheet.uciToCoords("a8h1")!!.toList(),
        )
    }

    @Test
    fun `UCI_COORDS_ROUNDTRIP - malformed strings yield null`() {
        assertEquals(null, TodaysDrillBottomSheet.uciToCoords(""))
        assertEquals(null, TodaysDrillBottomSheet.uciToCoords("e2"))
        assertEquals(null, TodaysDrillBottomSheet.uciToCoords("z9e4"))
        assertEquals(null, TodaysDrillBottomSheet.uciToCoords("e2e9"))
    }

    @Test
    fun `UCI_PROMO_CHAR - promotion letter surfaces, plain moves get a space`() {
        assertEquals('q', TodaysDrillBottomSheet.uciPromotionChar("e7e8q"))
        assertEquals('n', TodaysDrillBottomSheet.uciPromotionChar("a2a1n"))
        assertEquals(' ', TodaysDrillBottomSheet.uciPromotionChar("e2e4"))
    }

    // ── Walk status copy ─────────────────────────────────────────────

    @Test
    fun `WALK_STATUS_COPY - upfront depth then per-step progress`() {
        assertEquals(
            "This one runs deeper — find 2 moves.",
            TodaysDrillBottomSheet.formatWalkStatus(found = 0, total = 2),
        )
        assertEquals(
            "Correct — find the next move (1 of 2).",
            TodaysDrillBottomSheet.formatWalkStatus(found = 1, total = 2),
        )
        assertEquals(
            "Correct — find the next move (2 of 3).",
            TodaysDrillBottomSheet.formatWalkStatus(found = 2, total = 3),
        )
    }
}
