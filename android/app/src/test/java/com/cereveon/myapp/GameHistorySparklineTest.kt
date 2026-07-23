package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [GameHistoryBottomSheet.extractSparklineRatings].
 *
 * The function is pure (no Android context required) and must satisfy the
 * following invariants:
 *
 *  SPARK_EMPTY   — empty game list yields empty rating list.
 *  SPARK_NULL    — games with null ratingAfter are excluded.
 *  SPARK_ORDER   — result is chronological (oldest first, i.e. reversed vs server order).
 *  SPARK_LIMIT   — at most 10 games are considered regardless of list length.
 *  SPARK_ALL_NULL — list of all-null ratings yields empty output (no sparkline shown).
 *  SPARK_SINGLE  — single rated game yields a one-element list (sparkline won't render, handled by view).
 */
class GameHistorySparklineTest {

    private fun item(id: String, rating: Float?) = GameHistoryItem(
        id = id,
        result = "win",
        accuracy = 0.8f,
        ratingAfter = rating,
        createdAt = "2026-03-${id.padStart(2, '0')}T10:00:00",
    )

    @Test
    fun `SPARK_EMPTY - empty game list returns empty rating list`() {
        val result = GameHistoryBottomSheet.extractSparklineRatings(emptyList())
        assertEquals(emptyList<Float>(), result)
    }

    @Test
    fun `SPARK_NULL - games with null ratingAfter are excluded from sparkline`() {
        val games = listOf(
            item("1", null),
            item("2", 1500f),
            item("3", null),
        )
        // After take(10).reversed(): [item3-null, item2-1500, item1-null], mapNotNull → [1500f]
        val result = GameHistoryBottomSheet.extractSparklineRatings(games)
        assertEquals(listOf(1500f), result)
    }

    @Test
    fun `SPARK_ORDER - result is chronological oldest first`() {
        // Server returns newest-first; extractSparklineRatings must reverse.
        val games = listOf(
            item("3", 1300f),  // newest
            item("2", 1250f),
            item("1", 1200f),  // oldest
        )
        val result = GameHistoryBottomSheet.extractSparklineRatings(games)
        assertEquals(listOf(1200f, 1250f, 1300f), result)
    }

    @Test
    fun `SPARK_LIMIT - at most 10 games are considered`() {
        // 15 games newest-first: id 15 = newest (index 0), id 1 = oldest (index 14)
        val games = (15 downTo 1).map { i -> item("$i", (1200 + i).toFloat()) }
        val result = GameHistoryBottomSheet.extractSparklineRatings(games)
        assertTrue("Expected ≤10 ratings, got ${result.size}", result.size <= 10)
    }

    @Test
    fun `SPARK_LIMIT - exactly 10 most recent games used when list larger`() {
        // 15 games newest-first: id 15 = newest (rating 1215), id 1 = oldest (rating 1201).
        // take(10) selects games 15..6 (ratings 1215..1206, newest-first).
        // reversed() produces chronological order: game 6 first (1206), game 15 last (1215).
        val games = (15 downTo 1).map { i -> item("$i", (1200 + i).toFloat()) }
        val result = GameHistoryBottomSheet.extractSparklineRatings(games)
        assertEquals(10, result.size)
        assertEquals(1206f, result.first(), 0.01f)
        assertEquals(1215f, result.last(), 0.01f)
    }

    @Test
    fun `SPARK_ALL_NULL - all null ratingAfter yields empty list`() {
        val games = listOf(item("1", null), item("2", null))
        val result = GameHistoryBottomSheet.extractSparklineRatings(games)
        assertEquals(emptyList<Float>(), result)
    }

    @Test
    fun `SPARK_SINGLE - single rated game yields one-element list`() {
        val games = listOf(item("1", 1350f))
        val result = GameHistoryBottomSheet.extractSparklineRatings(games)
        assertEquals(listOf(1350f), result)
    }

    // --- Source provenance (Lichess games history view) ---------------------

    private fun sourced(source: String) = GameHistoryItem(
        id = "g",
        result = "win",
        accuracy = 0.8f,
        createdAt = "2026-03-01T10:00:00",
        source = source,
    )

    @Test
    fun `IMPORTED_TRUE - lichess source is imported, case-insensitive`() {
        assertTrue(GameHistoryBottomSheet.isImported(sourced("lichess")))
        assertTrue(GameHistoryBottomSheet.isImported(sourced("Lichess")))
    }

    @Test
    fun `IMPORTED_FALSE - app source and default are not imported`() {
        assertFalse(GameHistoryBottomSheet.isImported(sourced("app")))
        // Default source (payload from a server predating the field) is "app".
        assertFalse(GameHistoryBottomSheet.isImported(GameHistoryItem(id = "g", result = "win")))
    }

    @Test
    fun `FILTER_SOURCE_MAP - each tab maps to its server source value`() {
        assertEquals(null, GameHistoryBottomSheet.HistoryFilter.ALL.source)
        assertEquals("app", GameHistoryBottomSheet.HistoryFilter.APP.source)
        assertEquals("lichess", GameHistoryBottomSheet.HistoryFilter.LICHESS.source)
    }

    @Test
    fun `FILTER_LIMIT - source tabs request a deeper slice than All`() {
        // "All" keeps the 20-game recent window; source views request more
        // so they aren't truncated by unrelated recent games.
        assertEquals(20, GameHistoryBottomSheet.HistoryFilter.ALL.limit)
        assertTrue(GameHistoryBottomSheet.HistoryFilter.APP.limit > 20)
        assertTrue(GameHistoryBottomSheet.HistoryFilter.LICHESS.limit > 20)
    }
}
