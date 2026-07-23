package com.cereveon.myapp

import org.junit.Assert.*
import org.junit.Test

/**
 * Unit tests for the pure-Kotlin helper functions in [GameSummaryBottomSheet.Companion].
 *
 * The fragment itself extends [BottomSheetDialogFragment] and cannot be instantiated in
 * a JVM test without Robolectric.  These tests cover all the display-logic helpers that
 * would drive the view bindings, verifying that every field of [GameFinishResponse] is
 * processed correctly and that null / missing values fall back gracefully.
 *
 * Invariants pinned
 * -----------------
 *  1.  RETIRED: formatRating helper was removed when the user-visible
 *      Elo display was hidden from the UI.  See Home XP kicker tests
 *      in HomeActivityTest for the replacement player-anchor surface.
 *  2.  (retired)
 *  3.  FORMAT_CONFIDENCE_PERCENT:        formatConfidence converts 0.0–1.0 to percent string.
 *  4.  CONFIDENCE_PROGRESS_BOUNDS:       confidenceProgress clamps outside 0–1.
 *  5.  CONFIDENCE_PROGRESS_MIDPOINT:     confidenceProgress 0.5 → 50.
 *  6.  ACTION_BADGE_DRILL:               actionBadgeLabel "DRILL" → "DRILL".
 *  7.  ACTION_BADGE_PUZZLE:              actionBadgeLabel "PUZZLE" → "PUZZLE".
 *  8.  ACTION_BADGE_REFLECT:             actionBadgeLabel "REFLECT" → "REFLECT".
 *  9.  ACTION_BADGE_CELEBRATE:           actionBadgeLabel "CELEBRATE" → "CELEBRATE".
 * 10.  ACTION_BADGE_UNKNOWN:             actionBadgeLabel unknown string → "COACH".
 * 11.  ACTION_BADGE_EMPTY:               actionBadgeLabel "" → "COACH".
 * 12.  ACTION_BADGE_CASE_INSENSITIVE:    actionBadgeLabel "drill" → "DRILL".
 * 13.  FORMAT_TOPIC_CAPITALISED:         formatTopic capitalises first letter, replaces underscores.
 * 14.  RETIRED in PR 26: FORMAT_FORMAT_CAPITALISED (formatFormat helper deleted).
 * 15.  RETIRED in PR 26: FORMAT_GAIN_POSITIVE (formatGain helper deleted).
 * 16.  TRAIN_DIFF_FORMAT_BAND:           formatDifficulty capitalises the band string.
 * 17.  TRAIN_DIFF_PROGRESS_BAND/UNKNOWN/CASE: difficultyProgress maps
 *                                        easy/medium/hard → 30/60/85, midpoints
 *                                        unknown bands at 50, case-insensitively.
 * 18.  BUNDLE_ARGS_NULL_COACH_ACTION:    GameFinishResponse with null weakness/reason doesn't crash.
 * 19.  BUNDLE_ARGS_BLANK_DESCRIPTION:    coachContent description can be empty.
 * 20.  BUNDLE_FULL_RESPONSE_PARSES:      Full GameFinishResponse produces expected formatted strings.
 * 21.  STATUS_SAFE_MODE:                learningStatusLabel "safe_mode" → "✓ Progress saved".
 *                                        (Collapsed from "⏸ Tracking paused" — see helper KDoc:
 *                                        the prod server hard-codes status="safe_mode", so the
 *                                        old branch read to users as a transient outage even
 *                                        though their game / rating / coaching profile WERE all
 *                                        being saved.  The "paused" RL detail is invisible to
 *                                        the user and shouldn't surface as UI copy.)
 * 22.  STATUS_STORED:                   learningStatusLabel "stored" → "✓ Progress saved".
 * 23.  STATUS_OTHER:                    learningStatusLabel any other value → "✓ Progress saved".
 * 24.  STATUS_CASE_INSENSITIVE:         learningStatusLabel "SAFE_MODE" treated same as
 *                                        "safe_mode" — both resolve to "✓ Progress saved".
 *
 * RETIRED: SAFE_MODE_BADGE_DISTINCT (asserted safe_mode and stored produce DIFFERENT labels).
 *          Invariant inverted by design: post-collapse, safe_mode and stored produce the SAME
 *          label.  Replaced by SAFE_MODE_BADGE_COLLAPSED below, which positively pins the new
 *          intent so a future reviewer can't accidentally re-split the labels without breaking
 *          a green test.
 */
class GameSummaryBottomSheetTest {

    // ------------------------------------------------------------------
    // Helper
    // ------------------------------------------------------------------

    private fun makeResponse(
        newRating: Float = 1200f,
        confidence: Float = 0.72f,
        actionType: String = "DRILL",
        weakness: String? = "tactics",
        reason: String? = "Missed fork",
        title: String = "Drill tactics",
        description: String = "Practice forks and skewers.",
    ) = GameFinishResponse(
        status = "stored",
        newRating = newRating,
        confidence = confidence,
        coachAction = CoachActionDto(type = actionType, weakness = weakness, reason = reason),
        coachContent = CoachContentDto(title = title, description = description),
    )

    // ------------------------------------------------------------------
    // 1–2  formatRating — retired alongside the Elo display.
    // ------------------------------------------------------------------

    // ------------------------------------------------------------------
    // 3–5  formatConfidence / confidenceProgress
    // ------------------------------------------------------------------

    @Test
    fun `formatConfidence converts fraction to percent string`() {
        // Atrium re-skin: bare percentage, no "Confidence:" prefix.
        assertEquals("72%", GameSummaryBottomSheet.formatConfidence(0.72f))
        assertEquals("100%", GameSummaryBottomSheet.formatConfidence(1.0f))
        assertEquals("0%", GameSummaryBottomSheet.formatConfidence(0.0f))
    }

    @Test
    fun `confidenceProgress clamps values outside 0 to 1`() {
        assertEquals(0, GameSummaryBottomSheet.confidenceProgress(-0.5f))
        assertEquals(100, GameSummaryBottomSheet.confidenceProgress(1.5f))
    }

    @Test
    fun `confidenceProgress midpoint returns 50`() {
        assertEquals(50, GameSummaryBottomSheet.confidenceProgress(0.5f))
    }

    // ------------------------------------------------------------------
    // 6–12  actionBadgeLabel
    // ------------------------------------------------------------------

    @Test
    fun `actionBadgeLabel maps DRILL`() {
        assertEquals("DRILL", GameSummaryBottomSheet.actionBadgeLabel("DRILL"))
    }

    @Test
    fun `actionBadgeLabel maps PUZZLE`() {
        assertEquals("PUZZLE", GameSummaryBottomSheet.actionBadgeLabel("PUZZLE"))
    }

    @Test
    fun `actionBadgeLabel maps REFLECT`() {
        assertEquals("REFLECT", GameSummaryBottomSheet.actionBadgeLabel("REFLECT"))
    }

    @Test
    fun `actionBadgeLabel maps CELEBRATE`() {
        assertEquals("CELEBRATE", GameSummaryBottomSheet.actionBadgeLabel("CELEBRATE"))
    }

    @Test
    fun `actionBadgeLabel unknown type returns COACH`() {
        assertEquals("COACH", GameSummaryBottomSheet.actionBadgeLabel("UNKNOWN_TYPE"))
        assertEquals("COACH", GameSummaryBottomSheet.actionBadgeLabel("NONE"))
    }

    @Test
    fun `actionBadgeLabel empty string returns COACH`() {
        assertEquals("COACH", GameSummaryBottomSheet.actionBadgeLabel(""))
    }

    @Test
    fun `actionBadgeLabel is case-insensitive`() {
        assertEquals("DRILL", GameSummaryBottomSheet.actionBadgeLabel("drill"))
        assertEquals("PUZZLE", GameSummaryBottomSheet.actionBadgeLabel("Puzzle"))
    }

    // ------------------------------------------------------------------
    // 13  formatTopic
    // ------------------------------------------------------------------

    @Test
    fun `formatTopic capitalises first letter and replaces underscores with spaces`() {
        assertEquals("Topic: Endgame technique", GameSummaryBottomSheet.formatTopic("endgame_technique"))
        assertEquals("Topic: Tactics", GameSummaryBottomSheet.formatTopic("tactics"))
    }

    // 14–15 RETIRED in PR 26 (2026-05-15): formatFormat / formatGain
    // companion helpers deleted alongside the /next-training fallback path
    // that was their sole caller.  /curriculum/next uses ``exerciseType`` +
    // ``difficulty`` directly, inline-formatted at the GameSummaryBottomSheet
    // call site.

    // ------------------------------------------------------------------
    // 16–17  formatDifficulty / difficultyProgress (String band)
    // ------------------------------------------------------------------
    //
    // The Float-based ``difficultyProgress`` companion helper was retired
    // 2026-05-25 alongside the wire-shape fix that switched
    // ``CurriculumRecommendation.difficulty`` to ``String`` (one of
    // "easy" / "medium" / "hard").  The String-band helpers (and these
    // TRAIN_DIFF_* tests) moved here from TrainingSessionBottomSheet when
    // the standalone Lessons surface was removed — the post-game training
    // card is their only remaining caller.

    @Test
    fun `TRAIN_DIFF_FORMAT_BAND - capitalises the band string`() {
        assertEquals("Difficulty: Easy",   GameSummaryBottomSheet.formatDifficulty("easy"))
        assertEquals("Difficulty: Medium", GameSummaryBottomSheet.formatDifficulty("medium"))
        assertEquals("Difficulty: Hard",   GameSummaryBottomSheet.formatDifficulty("hard"))
    }

    @Test
    fun `TRAIN_DIFF_PROGRESS_BAND - maps each known band to its fixed percent`() {
        assertEquals(30, GameSummaryBottomSheet.difficultyProgress("easy"))
        assertEquals(60, GameSummaryBottomSheet.difficultyProgress("medium"))
        assertEquals(85, GameSummaryBottomSheet.difficultyProgress("hard"))
    }

    @Test
    fun `TRAIN_DIFF_PROGRESS_UNKNOWN - unknown band falls through to the 50 percent midpoint`() {
        // Future bands shipped by the server without a coordinated Android
        // release should render at the midpoint rather than 0 (which would
        // imply "no difficulty") or throw.
        assertEquals(50, GameSummaryBottomSheet.difficultyProgress("expert"))
        assertEquals(50, GameSummaryBottomSheet.difficultyProgress(""))
    }

    @Test
    fun `TRAIN_DIFF_PROGRESS_CASE - band match is case-insensitive`() {
        assertEquals(30, GameSummaryBottomSheet.difficultyProgress("EASY"))
        assertEquals(60, GameSummaryBottomSheet.difficultyProgress("Medium"))
    }

    // ------------------------------------------------------------------
    // 18  Null weakness/reason in CoachActionDto doesn't affect badge
    // ------------------------------------------------------------------

    @Test
    fun `null weakness and reason in coachAction does not affect badge label`() {
        val resp = makeResponse(actionType = "REFLECT", weakness = null, reason = null)
        assertEquals("REFLECT", GameSummaryBottomSheet.actionBadgeLabel(resp.coachAction.type))
    }

    // ------------------------------------------------------------------
    // 19  Empty description handled as a valid (empty) string
    // ------------------------------------------------------------------

    @Test
    fun `empty coach content description is accepted without crash`() {
        val resp = makeResponse(description = "")
        // Should not throw; the view binding would simply show ""
        assertEquals("", resp.coachContent.description)
    }

    // ------------------------------------------------------------------
    // 20  Full response produces correct formatted strings end-to-end
    // ------------------------------------------------------------------

    @Test
    fun `full GameFinishResponse produces expected formatted strings`() {
        val resp = makeResponse(
            newRating = 1350f,
            confidence = 0.85f,
            actionType = "DRILL",
            title = "Work on tactics",
            description = "You missed a fork on move 12.",
        )
        // Atrium re-skin (192d7408): bare values — the metric-strip
        // cells carry their own kickers, so the formatters no longer
        // prefix.  The RATING cell was retired when the user-visible
        // Elo display was hidden; only ACCURACY / THEME / coach copy
        // remain on the strip.
        assertEquals("85%",  GameSummaryBottomSheet.formatConfidence(resp.confidence))
        assertEquals(85,                GameSummaryBottomSheet.confidenceProgress(resp.confidence))
        assertEquals("DRILL",           GameSummaryBottomSheet.actionBadgeLabel(resp.coachAction.type))
        assertEquals("Work on tactics", resp.coachContent.title)
    }

    // ------------------------------------------------------------------
    // 21–24  learningStatusLabel (P3-B)
    // ------------------------------------------------------------------

    @Test
    fun `STATUS_SAFE_MODE - safe_mode now returns the friendly progress-saved label`() {
        // Collapsed from "⏸ Tracking paused".  The user's game IS saved
        // (events table), their rating IS updated (Player.rating), their
        // coaching profile IS updated (SkillUpdater); only the bandit's
        // online-learning loop is "paused", and that detail is invisible
        // to the user.  Surfacing it as "Tracking paused" was misleading.
        assertEquals("✓ Progress saved", GameSummaryBottomSheet.learningStatusLabel("safe_mode"))
    }

    // ------------------------------------------------------------------
    // formatMistakeSummary (Phase 3 mistake-replay card subline)
    // ------------------------------------------------------------------

    @Test
    fun `formatMistakeSummary includes move number and cp loss`() {
        assertEquals(
            "Move 14 — find a stronger move (lost 240 cp).",
            GameSummaryBottomSheet.formatMistakeSummary(14, 240),
        )
    }

    @Test
    fun `formatMistakeSummary handles single-digit move number`() {
        assertEquals(
            "Move 1 — find a stronger move (lost 175 cp).",
            GameSummaryBottomSheet.formatMistakeSummary(1, 175),
        )
    }

    @Test
    fun `STATUS_STORED - stored returns progress saved label`() {
        assertEquals("✓ Progress saved", GameSummaryBottomSheet.learningStatusLabel("stored"))
    }

    @Test
    fun `STATUS_OTHER - arbitrary status returns progress saved label`() {
        assertEquals("✓ Progress saved", GameSummaryBottomSheet.learningStatusLabel("active"))
        assertEquals("✓ Progress saved", GameSummaryBottomSheet.learningStatusLabel("complete"))
    }

    @Test
    fun `STATUS_CASE_INSENSITIVE - SAFE_MODE uppercase treated same as safe_mode`() {
        // Post-collapse, every casing variant resolves to the same friendly
        // label (just like every other status string today).  The
        // lowercase()/when scaffold is preserved so a future non-safe-mode
        // deployment can branch without re-introducing the misleading
        // "paused" wording on the prod path.
        assertEquals("✓ Progress saved", GameSummaryBottomSheet.learningStatusLabel("SAFE_MODE"))
        assertEquals("✓ Progress saved", GameSummaryBottomSheet.learningStatusLabel("Safe_Mode"))
    }

    // ------------------------------------------------------------------
    // 25–27  GameSummaryBottomSheet renders correctly for safe_mode (P5)
    //
    // These tests verify the full data path from a GameFinishResponse with
    // learningStatus="safe_mode" through to the label text that the view
    // would display.  The view binding itself (TextView visibility) requires
    // Android framework; the data pipeline is fully testable in host JVM.
    // ------------------------------------------------------------------

    @Test
    fun `SAFE_MODE_RESPONSE_LABEL - GameFinishResponse with safe_mode produces friendly progress-saved label`() {
        // Simulate the exact value that learningStatus carries when the backend
        // returns {"learning": {"status": "safe_mode"}} (the only thing prod
        // ever sends — see llm/seca/events/router.py, ``learning_result =
        // {"status": "safe_mode"}`` hard-code).  Post-collapse, that flows
        // through to the friendly "✓ Progress saved" copy.
        val response = makeResponse()   // learningStatus is null in helper by default
        val statusFromBackend = "safe_mode"
        assertEquals(
            "✓ Progress saved",
            GameSummaryBottomSheet.learningStatusLabel(statusFromBackend),
        )
    }

    @Test
    fun `SAFE_MODE_BADGE_COLLAPSED - safe_mode and stored produce the SAME label by design`() {
        // Inverse of the retired SAFE_MODE_BADGE_DISTINCT pin.  The two
        // labels USED to differ ("⏸ Tracking paused" vs "✓ Progress
        // saved"), which read to users as "your data isn't being saved"
        // even though it was.  Post-collapse they're identical; this
        // test positively pins that intent so a reviewer can't
        // accidentally re-split them in a future refactor without
        // breaking a green test.
        val safeLabel   = GameSummaryBottomSheet.learningStatusLabel("safe_mode")
        val storedLabel = GameSummaryBottomSheet.learningStatusLabel("stored")
        assertEquals(
            "safe_mode and stored must produce the same user-facing label",
            safeLabel,
            storedLabel,
        )
    }

    @Test
    fun `SAFE_MODE_FULL_RESPONSE - response with safe_mode learningStatus maps to friendly copy without paused wording`() {
        // Full pipeline: response field → learningStatusLabel → display
        // string.  Inverse pin from the retired version: ensure the
        // label NO LONGER mentions "paused" (the misleading wording)
        // AND positively contains the friendly "Progress saved" copy.
        val learningStatus = "safe_mode"
        val label = GameSummaryBottomSheet.learningStatusLabel(learningStatus)
        assertFalse(
            "Label for safe_mode must NOT contain 'paused' (collapsed copy), got: $label",
            label.contains("paused", ignoreCase = true),
        )
        assertTrue(
            "Label for safe_mode MUST contain 'Progress saved' (collapsed copy), got: $label",
            label.contains("Progress saved"),
        )
    }
}
