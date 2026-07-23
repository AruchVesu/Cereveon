package com.cereveon.myapp

import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset

/**
 * How long until a freemium usage limit resets.
 *
 * The server meters the coached-game and chat-turn limits on a ROLLING
 * 24h window anchored at first use (2026-07-22 — llm/seca/entitlements/
 * service.py `_ROLLING_WINDOW`) and returns the exact unlock instant as
 * `reset_at` on the 402 body (API_CONTRACTS.md §§5/11).  The client counts
 * down to THAT server instant, so the figure matches the server's gate to
 * the minute no matter when the window started.
 *
 * FALLBACK: when `reset_at` is absent (an older server, a null value, or an
 * unparseable string) the countdown degrades to the next UTC-midnight
 * boundary — the pre-2026-07-22 calendar-day behaviour — so the surface
 * always shows a sane wait rather than nothing.
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
     * Milliseconds from [nowMillis] until the server's [resetAtIso] instant
     * (the 402 body's `reset_at`), falling back to the next UTC midnight
     * when it is absent or unparseable.  May be non-positive if the window
     * is already due — [formatCountdown] renders that as "any moment now".
     */
    fun msUntilReset(resetAtIso: String?, nowMillis: Long): Long {
        val reset = parseServerReset(resetAtIso) ?: return msUntilReset(nowMillis)
        return Duration.between(Instant.ofEpochMilli(nowMillis), reset).toMillis()
    }

    /**
     * Parse the server's `reset_at`.  The backend emits a NAIVE UTC
     * timestamp (`datetime.utcnow().isoformat()`, e.g.
     * "2026-07-18T14:03:22.511000" — no offset), so a bare value is read as
     * UTC; an offset-bearing value (a possible future server change) is
     * honoured too.  Returns null for null/blank/malformed input so the
     * caller falls back to the calendar window.
     */
    private fun parseServerReset(iso: String?): Instant? {
        if (iso.isNullOrBlank()) return null
        return try {
            LocalDateTime.parse(iso).toInstant(ZoneOffset.UTC)
        } catch (_: Exception) {
            try {
                OffsetDateTime.parse(iso).toInstant()
            } catch (_: Exception) {
                null
            }
        }
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

    /** Countdown for "now" against the UTC-midnight fallback window. */
    fun countdown(nowMillis: Long = System.currentTimeMillis()): String =
        formatCountdown(msUntilReset(nowMillis))

    /**
     * Countdown for "now" against the server's [resetAtIso] instant,
     * falling back to the UTC-midnight window when it is absent — the
     * live-ticker entry point for the limit surfaces.
     */
    fun countdown(resetAtIso: String?, nowMillis: Long = System.currentTimeMillis()): String =
        formatCountdown(msUntilReset(resetAtIso, nowMillis))
}
