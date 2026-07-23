package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit tests for [NotificationsBottomSheet]'s pure companion helpers —
 * the section partitioning, badge label, and row-age formatting the
 * feed renders from.  No Robolectric: all functions are Android-free
 * by design (same pattern as GameHistoryBottomSheet's companions).
 *
 * Pinned invariants
 * -----------------
 * PARTITION_ORDER    unread first, read second; server order kept
 *                    inside each section.
 * PARTITION_EMPTY    empty feed → two empty sections.
 * BADGE_EXACT        counts 0..9 render verbatim.
 * BADGE_CAP          10+ renders as "9+" (badge circle never grows).
 * AGE_BUCKETS        now / minutes / hours / days boundaries.
 * AGE_MALFORMED      null / blank / garbage timestamps render "" not crash.
 */
class NotificationsBottomSheetTest {

    private fun item(id: String, readAt: String? = null) = NotificationItem(
        id = id,
        type = NotificationItem.TYPE_GAME_ANALYZED,
        title = "Review ready",
        body = "A game is ready.",
        readAt = readAt,
    )

    // PARTITION_ORDER
    @Test
    fun `partition keeps server order inside unread and read sections`() {
        val feed = listOf(
            item("u1"),
            item("r1", readAt = "2026-07-15T10:00:00"),
            item("u2"),
            item("r2", readAt = "2026-07-14T10:00:00"),
        )
        val (unread, read) = NotificationsBottomSheet.partitionFeed(feed)
        assertEquals(listOf("u1", "u2"), unread.map { it.id })
        assertEquals(listOf("r1", "r2"), read.map { it.id })
    }

    // PARTITION_EMPTY
    @Test
    fun `partition of empty feed is two empty lists`() {
        val (unread, read) = NotificationsBottomSheet.partitionFeed(emptyList())
        assertEquals(emptyList<NotificationItem>(), unread)
        assertEquals(emptyList<NotificationItem>(), read)
    }

    // BADGE_EXACT
    @Test
    fun `badge label renders exact counts up to nine`() {
        assertEquals("0", NotificationsBottomSheet.formatBadgeLabel(0))
        assertEquals("1", NotificationsBottomSheet.formatBadgeLabel(1))
        assertEquals("9", NotificationsBottomSheet.formatBadgeLabel(9))
    }

    // BADGE_CAP
    @Test
    fun `badge label caps double digits at nine plus`() {
        assertEquals("9+", NotificationsBottomSheet.formatBadgeLabel(10))
        assertEquals("9+", NotificationsBottomSheet.formatBadgeLabel(123))
    }

    // AGE_BUCKETS
    @Test
    fun `age buckets - now, minutes, hours, days`() {
        // 2026-07-16T12:00:00 UTC in epoch millis.
        val now = 1_784_203_200_000L

        assertEquals("now", NotificationsBottomSheet.formatAge("2026-07-16T11:59:30", now))
        assertEquals("5m ago", NotificationsBottomSheet.formatAge("2026-07-16T11:55:00", now))
        assertEquals("59m ago", NotificationsBottomSheet.formatAge("2026-07-16T11:01:00", now))
        assertEquals("1h ago", NotificationsBottomSheet.formatAge("2026-07-16T11:00:00", now))
        assertEquals("23h ago", NotificationsBottomSheet.formatAge("2026-07-15T13:00:00", now))
        assertEquals("1d ago", NotificationsBottomSheet.formatAge("2026-07-15T12:00:00", now))
        assertEquals("30d ago", NotificationsBottomSheet.formatAge("2026-06-16T12:00:00", now))
    }

    // AGE_BUCKETS (backend emits microseconds — LocalDateTime.parse must cope)
    @Test
    fun `age parses backend microsecond timestamps`() {
        val now = 1_784_203_200_000L
        // 11:54:30.123456 → 12:00:00 is 5m29.876s, floored to 5 minutes.
        assertEquals(
            "5m ago",
            NotificationsBottomSheet.formatAge("2026-07-16T11:54:30.123456", now),
        )
    }

    // AGE_MALFORMED
    @Test
    fun `age renders empty string on malformed input`() {
        val now = 1_784_203_200_000L
        assertEquals("", NotificationsBottomSheet.formatAge(null, now))
        assertEquals("", NotificationsBottomSheet.formatAge("", now))
        assertEquals("", NotificationsBottomSheet.formatAge("not-a-date", now))
    }
}
