package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.ZoneOffset
import java.time.ZonedDateTime

/**
 * Unit tests for [DailyLimitReset] — the freemium daily-limit reset
 * countdown shown on the game lock + chat quota surfaces (2026-07-22).
 *
 * Pins both reset sources: the server's rolling-24h `reset_at`
 * (llm/seca/entitlements/service.py `_ROLLING_WINDOW`, surfaced on the
 * 402 body) and the UTC-midnight FALLBACK used when it is absent, plus
 * the round-DOWN countdown formatting so the figure never overstates the
 * remaining wait.
 */
class DailyLimitResetTest {

    private fun utc(y: Int, mo: Int, d: Int, h: Int, mi: Int, s: Int = 0): Long =
        ZonedDateTime.of(y, mo, d, h, mi, s, 0, ZoneOffset.UTC).toInstant().toEpochMilli()

    @Test
    fun `MS_UNTIL_RESET - counts to the next UTC midnight`() {
        // 19:28:00Z → 04:32:00 remaining.
        assertEquals(
            (4L * 60 + 32) * 60_000L,
            DailyLimitReset.msUntilReset(utc(2026, 7, 22, 19, 28)),
        )
    }

    @Test
    fun `MS_UNTIL_RESET - just after midnight is nearly a full day`() {
        // 00:00:30Z → 23h 59m 30s remaining.
        assertEquals(
            (23L * 3600 + 59 * 60 + 30) * 1000L,
            DailyLimitReset.msUntilReset(utc(2026, 7, 22, 0, 0, 30)),
        )
    }

    @Test
    fun `MS_UNTIL_RESET - exactly midnight is a full 24h`() {
        assertEquals(24L * 3600 * 1000L, DailyLimitReset.msUntilReset(utc(2026, 7, 22, 0, 0, 0)))
    }

    @Test
    fun `FORMAT - hours and minutes`() {
        assertEquals("4h 32m", DailyLimitReset.formatCountdown((4L * 60 + 32) * 60_000L))
    }

    @Test
    fun `FORMAT - whole hours drop the minutes`() {
        assertEquals("3h", DailyLimitReset.formatCountdown(3L * 3600 * 1000L))
    }

    @Test
    fun `FORMAT - minutes only`() {
        assertEquals("43m", DailyLimitReset.formatCountdown(43L * 60_000L))
    }

    @Test
    fun `FORMAT - rounds DOWN so it never overstates the wait`() {
        // 4h 32m 59s → "4h 32m", not "4h 33m".
        assertEquals("4h 32m", DailyLimitReset.formatCountdown(((4L * 60 + 32) * 60 + 59) * 1000L))
    }

    @Test
    fun `FORMAT - under a minute`() {
        assertEquals("under a minute", DailyLimitReset.formatCountdown(30_000L))
    }

    @Test
    fun `FORMAT - non-positive reads any moment now`() {
        assertEquals("any moment now", DailyLimitReset.formatCountdown(0L))
        assertEquals("any moment now", DailyLimitReset.formatCountdown(-5_000L))
    }

    @Test
    fun `COUNTDOWN - end-to-end from epoch millis`() {
        assertEquals("4h 32m", DailyLimitReset.countdown(utc(2026, 7, 22, 19, 28)))
    }

    // ── server reset_at (rolling 24h window) ─────────────────────────

    @Test
    fun `MS_UNTIL_RESET reset_at - counts to the naive-UTC server instant`() {
        // The server emits datetime.utcnow().isoformat() with NO offset, so
        // the bare value is read as UTC.  12:00:00Z now → reset_at
        // 2026-07-23T11:30:00 = 23h 30m out.
        assertEquals(
            (23L * 60 + 30) * 60_000L,
            DailyLimitReset.msUntilReset("2026-07-23T11:30:00", utc(2026, 7, 22, 12, 0)),
        )
    }

    @Test
    fun `MS_UNTIL_RESET reset_at - tolerates Python microseconds`() {
        // "…T12:00:00.500000" = 0.5s past the whole minute; from exactly 24h
        // earlier the span is 24h + 500ms.
        assertEquals(
            24L * 3600 * 1000L + 500L,
            DailyLimitReset.msUntilReset("2026-07-23T12:00:00.500000", utc(2026, 7, 22, 12, 0)),
        )
    }

    @Test
    fun `MS_UNTIL_RESET reset_at - honours an explicit offset`() {
        // A future server variant could add a Z / +00:00 offset — still parse.
        assertEquals(
            3_600_000L,
            DailyLimitReset.msUntilReset("2026-07-22T13:00:00Z", utc(2026, 7, 22, 12, 0)),
        )
    }

    @Test
    fun `MS_UNTIL_RESET reset_at - falls back to UTC midnight when absent or unparseable`() {
        val now = utc(2026, 7, 22, 19, 28) // 4h 32m to the next UTC midnight
        val expected = (4L * 60 + 32) * 60_000L
        assertEquals(expected, DailyLimitReset.msUntilReset(null, now))
        assertEquals(expected, DailyLimitReset.msUntilReset("", now))
        assertEquals(expected, DailyLimitReset.msUntilReset("not-a-date", now))
    }

    @Test
    fun `COUNTDOWN reset_at - formats the rolling wait just after the window opens`() {
        // reset_at = first-use + 24h, evaluated ~1 min in → "23h 59m": the
        // "must write 24 h" case the rolling window fixed (was time-to-midnight).
        assertEquals(
            "23h 59m",
            DailyLimitReset.countdown("2026-07-23T11:59:00", utc(2026, 7, 22, 12, 0)),
        )
    }

    @Test
    fun `COUNTDOWN reset_at - null falls back to the midnight window`() {
        assertEquals("4h 32m", DailyLimitReset.countdown(null, utc(2026, 7, 22, 19, 28)))
    }
}
