package ai.chesscoach.app

import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset

/**
 * How long until the freemium DAILY limits reset.
 *
 * The server keys usage by the UTC calendar day
 * (llm/seca/entitlements/service.py `_period_key` → `strftime("%Y-%m-%d")`),
 * so a daily limit's counter resets at the next UTC midnight.  That reset
 * is deterministic, so the client computes the countdown locally — no
 * server round-trip and no reset field on the 402 body.
 *
 * COUPLING: this mirrors the server's UTC-calendar reset window.  If the
 * server ever moves to a rolling 24h window, change this + its test
 * together (pinned by DailyLimitResetTest).
 *
 * A countdown (duration) rather than a wall-clock time is deliberate: it
 * reads the same in every timezone, so a user in UTC+3 and one in UTC-5
 * both see the true remaining wait without a confusing "00:00 UTC".
 */
object DailyLimitReset {

    private const val MS_PER_MINUTE = 60_000L

    /** Milliseconds from [nowMillis] (Unix epoch) until the next UTC midnight. */
    fun msUntilReset(nowMillis: Long): Long {
        val now = Instant.ofEpochMilli(nowMillis).atZone(ZoneOffset.UTC)
        val nextMidnight = now.toLocalDate().plusDays(1).atStartOfDay(ZoneOffset.UTC)
        return Duration.between(now, nextMidnight).toMillis()
    }

    /**
     * Human countdown for a remaining-[ms] span: "4h 32m", "43m",
     * "under a minute".  Rounds DOWN to the whole minute so the figure
     * never overstates the wait; a non-positive span reads "any moment
     * now" (the reset is due).
     */
    fun formatCountdown(ms: Long): String {
        if (ms <= 0L) return "any moment now"
        val totalMinutes = ms / MS_PER_MINUTE
        val hours = totalMinutes / 60L
        val minutes = totalMinutes % 60L
        return when {
            hours > 0L && minutes > 0L -> "${hours}h ${minutes}m"
            hours > 0L -> "${hours}h"
            minutes > 0L -> "${minutes}m"
            else -> "under a minute"
        }
    }

    /** Countdown string for "now" — the live-ticker entry point. */
    fun countdown(nowMillis: Long = System.currentTimeMillis()): String =
        formatCountdown(msUntilReset(nowMillis))
}
