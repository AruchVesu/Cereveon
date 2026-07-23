package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Host-JVM tests for the pure mapping helpers behind the review UI —
 * [GameReviewBottomSheet] companion + [ReviewEvalGraphView] companion.
 * Same pattern as GameHistorySparklineTest: companions on fragments /
 * views are class-loadable on the host JVM as long as nothing is
 * instantiated.
 *
 * Pinned invariants
 * -----------------
 * MAP_BAND_LEVEL     five bands map to levels 0..4; unknown clamps to 2.
 * MAP_NEGATIVE       blunder/missed_win/mistake amber; punished/strategic not.
 * MAP_ACTION         action per status/outcome incl. the upgrade CTA.
 * MAP_STATUS_LINE    per-wave status copy.
 * MAP_META_LINE      opponent picked from the OTHER side; null engine → "".
 * MAP_STATS_LINE     accuracy % + singular/plural counts.
 * MAP_MOMENT_TEXT    header + banded transition line.
 * MAP_QUOTA          quota line renders only with limit+remaining present.
 * MAP_TERMINAL       isTerminal only for complete/failed.
 */
class GameReviewMappingTest {

    private fun review(
        status: String,
        outcome: String? = null,
        entitlement: ReviewEntitlement? = null,
    ) = GameReviewResponse(
        reviewId = "r1",
        eventId = "e1",
        status = status,
        llm = outcome?.let { ReviewLlm(outcome = it) },
        entitlement = entitlement,
    )

    @Test
    fun `MAP_BAND_LEVEL - bands map to ordered levels and unknown clamps mid`() {
        assertEquals(0, ReviewEvalGraphView.bandLevel("losing"))
        assertEquals(1, ReviewEvalGraphView.bandLevel("worse"))
        assertEquals(2, ReviewEvalGraphView.bandLevel("equal"))
        assertEquals(3, ReviewEvalGraphView.bandLevel("better"))
        assertEquals(4, ReviewEvalGraphView.bandLevel("winning"))
        assertEquals(2, ReviewEvalGraphView.bandLevel("mystery_band"))
        assertEquals(2, ReviewEvalGraphView.bandLevel(null))
    }

    @Test
    fun `MAP_NEGATIVE - amber role for error types only`() {
        assertTrue(ReviewEvalGraphView.isNegativeType(ReviewMoment.TYPE_BLUNDER))
        assertTrue(ReviewEvalGraphView.isNegativeType(ReviewMoment.TYPE_MISSED_WIN))
        assertTrue(ReviewEvalGraphView.isNegativeType(ReviewMoment.TYPE_MISTAKE))
        assertFalse(ReviewEvalGraphView.isNegativeType(ReviewMoment.TYPE_PUNISHED_MISTAKE))
        assertFalse(ReviewEvalGraphView.isNegativeType(ReviewMoment.TYPE_STRATEGIC))
    }

    @Test
    fun `MAP_ACTION - per status and outcome`() {
        assertEquals(
            GameReviewBottomSheet.Companion.ReviewAction.RETRY_FAILED,
            GameReviewBottomSheet.actionFor(review("failed")),
        )
        assertEquals(
            GameReviewBottomSheet.Companion.ReviewAction.NONE,
            GameReviewBottomSheet.actionFor(review("engine_done")),
        )
        assertEquals(
            GameReviewBottomSheet.Companion.ReviewAction.NONE,
            GameReviewBottomSheet.actionFor(review("complete", outcome = "full")),
        )
        assertEquals(
            GameReviewBottomSheet.Companion.ReviewAction.RETRY_COACH,
            GameReviewBottomSheet.actionFor(review("complete", outcome = "fallback")),
        )
        assertEquals(
            GameReviewBottomSheet.Companion.ReviewAction.UPGRADE,
            GameReviewBottomSheet.actionFor(review("complete", outcome = "skipped_entitlement")),
        )
    }

    @Test
    fun `MAP_STATUS_LINE - per-wave copy`() {
        assertEquals("Analyzing the game…", GameReviewBottomSheet.statusLine(review("queued")))
        assertEquals("Analyzing the game…", GameReviewBottomSheet.statusLine(review("running")))
        assertEquals(
            "Coach is looking at the game…",
            GameReviewBottomSheet.statusLine(review("engine_done")),
        )
        assertEquals(
            "Review ready.",
            GameReviewBottomSheet.statusLine(review("complete", outcome = "full")),
        )
        assertTrue(
            GameReviewBottomSheet.statusLine(review("complete", outcome = "skipped_entitlement"))
                .contains("Pro"),
        )
        assertTrue(
            GameReviewBottomSheet.statusLine(review("failed")).contains("Try again"),
        )
    }

    @Test
    fun `MAP_META_LINE - opponent is the other seat`() {
        val meta = ReviewMeta(
            white = "me", black = "them",
            whiteElo = "1200", blackElo = "1450",
            timeControl = "600+5", opening = "Ruy Lopez",
        )
        val asWhite = ReviewEngine(playerColor = "white", meta = meta)
        assertEquals("vs them (1450) · 600+5 · Ruy Lopez", GameReviewBottomSheet.metaLine(asWhite))

        val asBlack = ReviewEngine(playerColor = "black", meta = meta)
        assertEquals("vs me (1200) · 600+5 · Ruy Lopez", GameReviewBottomSheet.metaLine(asBlack))

        assertEquals("", GameReviewBottomSheet.metaLine(null))
    }

    @Test
    fun `MAP_STATS_LINE - accuracy percent and plurals`() {
        val engine = ReviewEngine(
            accuracy = 0.615f,
            counts = ReviewCounts(blunders = 1, mistakes = 0, inaccuracies = 2),
        )
        assertEquals(
            "61% acc · 1 blunder · 0 mistakes · 2 inaccuracies",
            GameReviewBottomSheet.statsLine(engine),
        )
        val single = ReviewEngine(
            accuracy = 1f,
            counts = ReviewCounts(blunders = 0, mistakes = 1, inaccuracies = 1),
        )
        assertEquals(
            "100% acc · 0 blunders · 1 mistake · 1 inaccuracy",
            GameReviewBottomSheet.statsLine(single),
        )
    }

    @Test
    fun `MAP_MOMENT_TEXT - header and transition`() {
        val moment = ReviewMoment(
            ply = 21, moveNumber = 11, san = "Nbd2",
            momentType = "blunder", phase = "middlegame",
            bandBefore = "equal", bandAfter = "losing",
        )
        assertEquals("MOVE 11 · MIDDLEGAME", GameReviewBottomSheet.momentHeader(moment))
        assertEquals(
            "level → clearly losing",
            GameReviewBottomSheet.momentTransition(moment),
        )
    }

    @Test
    fun `MAP_MOMENT_TEXT - unchanged band reads stayed, not an arrow to itself`() {
        // "clearly losing → clearly losing" read as a glitch on-device
        // (2026-07-15): a blunder played while already deep in a losing
        // band doesn't move the five-step band, so say the band HELD.
        val unchanged = ReviewMoment(
            ply = 23, moveNumber = 12, san = "Qe3",
            momentType = "blunder", phase = "opening",
            bandBefore = "losing", bandAfter = "losing",
        )
        assertEquals(
            "stayed clearly losing",
            GameReviewBottomSheet.momentTransition(unchanged),
        )
    }

    @Test
    fun `MAP_QUOTA - renders only with limit and remaining`() {
        assertNull(GameReviewBottomSheet.quotaLine(null))
        assertNull(GameReviewBottomSheet.quotaLine(ReviewEntitlement(remaining = null)))
        assertNull(GameReviewBottomSheet.quotaLine(ReviewEntitlement(remaining = 2, limit = null)))
        assertEquals(
            "2 of 3 coach reviews left this month.",
            GameReviewBottomSheet.quotaLine(ReviewEntitlement(remaining = 2, limit = 3)),
        )
    }

    @Test
    fun `MAP_QUOTA - daily bucket reads today, monthly reads this month`() {
        // The server reports the BINDING bucket (pro 10/day smoothing cap
        // vs the monthly ceiling) via `metric` — the copy must follow.
        assertEquals(
            "4 of 10 coach reviews left today.",
            GameReviewBottomSheet.quotaLine(
                ReviewEntitlement(
                    metric = ReviewEntitlement.METRIC_DAILY, remaining = 4, limit = 10,
                )
            ),
        )
        assertEquals(
            "12 of 50 coach reviews left this month.",
            GameReviewBottomSheet.quotaLine(
                ReviewEntitlement(
                    metric = ReviewEntitlement.METRIC_MONTHLY, remaining = 12, limit = 50,
                )
            ),
        )
    }

    @Test
    fun `MAP_ACTION - capped pro gets no upgrade button`() {
        // A subscriber hitting the daily/monthly cap has nothing to buy;
        // UPGRADE reads as a bug. Free (or unknown plan) keeps the CTA.
        val proCapped = review(
            "complete",
            outcome = "skipped_entitlement",
            entitlement = ReviewEntitlement(plan = ReviewEntitlement.PLAN_PRO),
        )
        assertEquals(
            GameReviewBottomSheet.Companion.ReviewAction.NONE,
            GameReviewBottomSheet.actionFor(proCapped),
        )
        val freeCapped = review(
            "complete",
            outcome = "skipped_entitlement",
            entitlement = ReviewEntitlement(plan = "free"),
        )
        assertEquals(
            GameReviewBottomSheet.Companion.ReviewAction.UPGRADE,
            GameReviewBottomSheet.actionFor(freeCapped),
        )
    }

    @Test
    fun `MAP_STATUS_LINE - capped copy names the binding window`() {
        val daily = review(
            "complete",
            outcome = "skipped_entitlement",
            entitlement = ReviewEntitlement(
                metric = ReviewEntitlement.METRIC_DAILY,
                plan = ReviewEntitlement.PLAN_PRO,
            ),
        )
        assertTrue(GameReviewBottomSheet.statusLine(daily).contains("tomorrow"))

        val proMonthly = review(
            "complete",
            outcome = "skipped_entitlement",
            entitlement = ReviewEntitlement(
                metric = ReviewEntitlement.METRIC_MONTHLY,
                plan = ReviewEntitlement.PLAN_PRO,
            ),
        )
        assertTrue(GameReviewBottomSheet.statusLine(proMonthly).contains("Monthly"))
    }

    @Test
    fun `MAP_TERMINAL - only complete and failed stop the poll`() {
        assertFalse(review("queued").isTerminal)
        assertFalse(review("running").isTerminal)
        assertFalse(review("engine_done").isTerminal)
        assertTrue(review("complete").isTerminal)
        assertTrue(review("failed").isTerminal)
    }
}
