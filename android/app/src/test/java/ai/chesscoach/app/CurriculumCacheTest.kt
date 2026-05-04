package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the pure helpers and SharedPreferences key constants used in
 * curriculum persistence (P2-C) and weakness-tag display (P2-A).
 *
 * All tested functions live in [MainActivity.Companion] and have no Android
 * framework dependencies — safe to run in the host JVM test suite.
 *
 * Invariants:
 *  CACHE_CHIP_FORMAT      formatCurriculumChip produces "↳ TYPE: topic" text.
 *  CACHE_CHIP_NULL_TYPE   null exercise type falls back to "TRAIN".
 *  CACHE_CHIP_UNDERSCORE  topic underscores are replaced with spaces.
 *  CACHE_TAGS_EMPTY       formatWeaknessTags("") returns empty string.
 *  CACHE_TAGS_SORT        top entries are sorted by descending weakness score.
 *  CACHE_TAGS_LIMIT       at most maxTags entries are shown.
 *  CACHE_TAGS_HIGH_ARROW  entries with score ≥ 0.5 get "↑" prefix.
 *  CACHE_TAGS_LOW_ARROW   entries with score < 0.5 get "↓" prefix.
 *  CACHE_KEY_TOPIC        PREF_CURRICULUM_TOPIC constant is non-empty.
 *  CACHE_KEY_DIFF         PREF_CURRICULUM_DIFFICULTY constant is non-empty.
 *  CACHE_KEY_TYPE         PREF_CURRICULUM_EXERCISE_TYPE constant is non-empty.
 *  CACHE_KEY_CONFIDENCE   PREF_CONFIDENCE constant is non-empty.
 *  CACHE_KEY_DISTINCT     all three curriculum pref key constants are distinct.
 */
class CurriculumCacheTest {

    // ─────────────────────────────────────────────────────────────────────────
    // formatCurriculumChip
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `CACHE_CHIP_FORMAT - chip text follows arrow TYPE colon topic pattern`() {
        val result = MainActivity.formatCurriculumChip("endgame_technique", "drill")
        assertEquals("↳ DRILL: endgame technique", result)
    }

    @Test
    fun `CACHE_CHIP_NULL_TYPE - null exercise type renders as TRAIN`() {
        val result = MainActivity.formatCurriculumChip("tactics", null)
        assertEquals("↳ TRAIN: tactics", result)
    }

    @Test
    fun `CACHE_CHIP_UNDERSCORE - underscores in topic are replaced with spaces`() {
        val result = MainActivity.formatCurriculumChip("king_and_pawn", "puzzle")
        assertTrue(
            "Underscores must be replaced with spaces in chip text",
            result.contains("king and pawn"),
        )
    }

    // ─────────────────────────────────────────────────────────────────────────
    // formatWeaknessTags
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `CACHE_TAGS_EMPTY - empty skill vector returns empty string`() {
        assertEquals("", MainActivity.formatWeaknessTags(emptyMap()))
    }

    @Test
    fun `CACHE_TAGS_SORT - highest weakness score appears first in output`() {
        val sv = mapOf("endgame" to 0.3f, "tactics" to 0.9f, "opening" to 0.5f)
        val result = MainActivity.formatWeaknessTags(sv)
        val firstTag = result.substringBefore(" ·")
        assertTrue(
            "tactics (score 0.9) should appear first, got: $result",
            "tactics" in firstTag,
        )
    }

    @Test
    fun `CACHE_TAGS_LIMIT - at most maxTags entries appear in output`() {
        val sv = mapOf("a" to 0.9f, "b" to 0.8f, "c" to 0.7f, "d" to 0.6f)
        val result = MainActivity.formatWeaknessTags(sv, maxTags = 2)
        val count = result.split(" · ").size
        assertEquals("Expected exactly 2 tags, got: $result", 2, count)
        assertFalse("Fourth entry 'd' must not appear", "d" in result)
    }

    @Test
    fun `CACHE_TAGS_HIGH_ARROW - entries with score gte 0_5 carry up arrow`() {
        val sv = mapOf("tactics" to 0.75f)
        val result = MainActivity.formatWeaknessTags(sv)
        assertTrue("High weakness (0.75) must use ↑, got: $result", result.startsWith("↑"))
    }

    @Test
    fun `CACHE_TAGS_LOW_ARROW - entries with score lt 0_5 carry down arrow`() {
        val sv = mapOf("opening" to 0.3f)
        val result = MainActivity.formatWeaknessTags(sv)
        assertTrue("Low weakness (0.3) must use ↓, got: $result", result.startsWith("↓"))
    }

    @Test
    fun `CACHE_TAGS_BOUNDARY - score exactly 0_5 is treated as high weakness`() {
        val sv = mapOf("endgame" to 0.5f)
        val result = MainActivity.formatWeaknessTags(sv)
        assertTrue("Score 0.5 is the ≥ boundary for ↑, got: $result", result.startsWith("↑"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // SharedPreferences key constants
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `CACHE_KEY_TOPIC - PREF_CURRICULUM_TOPIC constant is non-empty`() {
        assertTrue(MainActivity.PREF_CURRICULUM_TOPIC.isNotEmpty())
    }

    @Test
    fun `CACHE_KEY_DIFF - PREF_CURRICULUM_DIFFICULTY constant is non-empty`() {
        assertTrue(MainActivity.PREF_CURRICULUM_DIFFICULTY.isNotEmpty())
    }

    @Test
    fun `CACHE_KEY_TYPE - PREF_CURRICULUM_EXERCISE_TYPE constant is non-empty`() {
        assertTrue(MainActivity.PREF_CURRICULUM_EXERCISE_TYPE.isNotEmpty())
    }

    @Test
    fun `CACHE_KEY_CONFIDENCE - PREF_CONFIDENCE constant is non-empty`() {
        assertTrue(MainActivity.PREF_CONFIDENCE.isNotEmpty())
    }

    @Test
    fun `CACHE_KEY_DISTINCT - all three curriculum pref key constants are distinct`() {
        val keys = setOf(
            MainActivity.PREF_CURRICULUM_TOPIC,
            MainActivity.PREF_CURRICULUM_DIFFICULTY,
            MainActivity.PREF_CURRICULUM_EXERCISE_TYPE,
        )
        assertEquals("Curriculum pref key constants must all be distinct", 3, keys.size)
    }
}
