package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit

/**
 * Pure-Kotlin unit tests for the static helpers on
 * [HomeActivity.Companion].  Like the Onboarding tests these run on
 * the host JVM without instrumentation since the helpers do not touch
 * the Android framework.
 *
 * Invariants pinned
 * -----------------
 *  1. initialsFor returns "—" for null/blank/"demo" so the avatar
 *     never displays a misleading default.
 *  2. initialsFor returns the first two alphanumeric chars uppercased
 *     for any other identifier.
 *  3. initialsFor pads to two chars by repeating the first when the
 *     id has only one alphanumeric char.
 *  4. formatDateKicker renders "<Weekday> · Day <NNN>" with N floored
 *     at 1 (same-day visit reads as "Day 001", not "Day 000").
 *  5. formatDateKicker advances by exactly one day per 24h delta.
 */
class HomeActivityTest {

    @Test
    fun `initialsFor returns dash for null blank or demo`() {
        assertEquals("—", HomeActivity.initialsFor(null))
        assertEquals("—", HomeActivity.initialsFor(""))
        assertEquals("—", HomeActivity.initialsFor("   "))
        assertEquals("—", HomeActivity.initialsFor("demo"))
        assertEquals("—", HomeActivity.initialsFor("DEMO"))
    }

    @Test
    fun `initialsFor returns first two alphanumeric chars uppercased`() {
        assertEquals("AG", HomeActivity.initialsFor("ag"))
        // Hyphens / non-alnum are stripped first, so "artiom-gusev"
        // collapses to "artiomgusev" and the leading two letters are
        // 'a' and 'r' — NOT 'a' and the leading char of the second
        // hyphen segment.
        assertEquals("AR", HomeActivity.initialsFor("artiom-gusev"))
        assertEquals("12", HomeActivity.initialsFor("12345-uuid-tail"))
    }

    @Test
    fun `initialsFor doubles a single alphanumeric char`() {
        assertEquals("AA", HomeActivity.initialsFor("a"))
        assertEquals("XX", HomeActivity.initialsFor("x---"))
    }

    @Test
    fun `initialsFor returns dash when there are no alphanumerics`() {
        assertEquals("—", HomeActivity.initialsFor("---"))
        assertEquals("—", HomeActivity.initialsFor("   "))
    }

    @Test
    fun `formatDateKicker shows Day 001 on the first visit`() {
        // Use UTC + a parsed date string so the assertion is independent
        // of the runner's TZ and the test author isn't responsible for
        // a magic millis literal.
        withUtc {
            val tueMillis = parseUtcDate("2026-04-21")  // Tuesday
            val kicker = HomeActivity.formatDateKicker(tueMillis, tueMillis)
            assertEquals("Tuesday · Day 001", kicker)
        }
    }

    @Test
    fun `formatDateKicker advances by one day per 24h`() {
        withUtc {
            val firstSeen = parseUtcDate("2026-04-21")  // Tuesday
            val sevenDaysLater = firstSeen + TimeUnit.DAYS.toMillis(7)
            val kicker = HomeActivity.formatDateKicker(sevenDaysLater, firstSeen)
            // 7 calendar days after a Tuesday is the next Tuesday.
            assertEquals("Tuesday · Day 008", kicker)
        }
    }

    @Test
    fun `formatDateKicker pads three digits even at high day counts`() {
        withUtc {
            val firstSeen = parseUtcDate("2026-04-21")
            val day47 = firstSeen + TimeUnit.DAYS.toMillis(46)  // 47th day inclusive
            val kicker = HomeActivity.formatDateKicker(day47, firstSeen)
            assertTrue(
                "expected kicker to end in Day 047, got $kicker",
                kicker.endsWith("Day 047"),
            )
        }
    }

    @Test
    fun `formatDateKicker floors at Day 001 even with clock skew`() {
        withUtc {
            val firstSeen = parseUtcDate("2026-04-21")
            // Now is BEFORE firstSeen (clock-skew or device-time-set
            // backwards); we never want the kicker to read "Day 000"
            // or "Day -005" — floor at 1.
            val skewed = firstSeen - TimeUnit.DAYS.toMillis(5)
            val kicker = HomeActivity.formatDateKicker(skewed, firstSeen)
            assertTrue(
                "expected kicker to end in Day 001 even with skew, got $kicker",
                kicker.endsWith("Day 001"),
            )
        }
    }

    // ── Resume card helpers ──────────────────────────────────────────

    @Test
    fun `formatResumeTitle pads the game number to 3 digits`() {
        assertEquals("Game 001 · move 0", HomeActivity.formatResumeTitle(1, 0))
        assertEquals("Game 047 · move 14", HomeActivity.formatResumeTitle(47, 14))
        assertEquals("Game 999 · move 42", HomeActivity.formatResumeTitle(999, 42))
    }

    @Test
    fun `formatResumeTitle clamps the game number floor at 1`() {
        // Defensive: a 0 / negative game number (corrupt or fresh-install
        // edge) shouldn't render as "Game 000" or "Game -005".
        assertEquals("Game 001 · move 5", HomeActivity.formatResumeTitle(0, 5))
        assertEquals("Game 001 · move 5", HomeActivity.formatResumeTitle(-3, 5))
    }

    @Test
    fun `formatResumeSub renders adaptive opponent without the rating number`() {
        // After Elo was hidden from the UI the resume sub no longer
        // exposes the rating-derived opponent number; it always
        // reads "vs. adaptive · HH:mm" so the user can't infer the
        // hidden rating from the displayed opponent strength.
        withUtc {
            val noon = parseUtcDateTime("2026-04-21T12:34:00Z")
            assertEquals("vs. adaptive · 12:34", HomeActivity.formatResumeSub(noon))
        }
    }

    @Test
    fun `formatResumeSub is independent of any cached rating`() {
        // The function takes only a timestamp now — there is no rating
        // parameter and the cached PREF_RATING is no longer consulted
        // when building this string.  Smoke-test the timestamp branch
        // at a few wall-clock values.
        withUtc {
            val morning = parseUtcDateTime("2026-04-21T08:05:00Z")
            val evening = parseUtcDateTime("2026-04-21T21:59:00Z")
            assertEquals("vs. adaptive · 08:05", HomeActivity.formatResumeSub(morning))
            assertEquals("vs. adaptive · 21:59", HomeActivity.formatResumeSub(evening))
        }
    }

    // ── XP kicker helper ─────────────────────────────────────────────

    @Test
    fun `formatXpKicker renders Level 1 0 XP for a fresh player`() {
        assertEquals("Level 1 · 0 XP", HomeActivity.formatXpKicker(0))
    }

    @Test
    fun `formatXpKicker increments level every XP_PER_LEVEL xp`() {
        // Linear curve documented on HomeActivity.XP_PER_LEVEL: each
        // bucket of XP_PER_LEVEL xp earns one level, starting at 1.
        val perLevel = HomeActivity.XP_PER_LEVEL
        assertEquals("Level 1 · ${perLevel - 1} XP", HomeActivity.formatXpKicker(perLevel - 1))
        assertEquals("Level 2 · $perLevel XP", HomeActivity.formatXpKicker(perLevel))
        assertEquals("Level 3 · ${perLevel * 2} XP", HomeActivity.formatXpKicker(perLevel * 2))
        assertEquals("Level 11 · ${perLevel * 10} XP", HomeActivity.formatXpKicker(perLevel * 10))
    }

    @Test
    fun `formatXpKicker clamps negative xp at 0`() {
        // Defensive: a malformed cache (e.g. PREF_TRAINING_XP
        // accidentally read as -1 sentinel) must not render
        // "Level 0 · -1 XP" or anything sub-zero — clamp to the
        // fresh-player presentation.
        assertEquals("Level 1 · 0 XP", HomeActivity.formatXpKicker(-1))
        assertEquals("Level 1 · 0 XP", HomeActivity.formatXpKicker(-500))
    }

    // ── helpers ──────────────────────────────────────────────────────

    private fun parseUtcDateTime(iso: String): Long {
        val fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }
        return fmt.parse(iso)!!.time
    }

    private fun parseUtcDate(iso: String): Long {
        val fmt = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }
        return fmt.parse(iso)!!.time
    }

    private inline fun withUtc(block: () -> Unit) {
        val tz = TimeZone.getDefault()
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"))
        try {
            block()
        } finally {
            TimeZone.setDefault(tz)
        }
    }

    // ── formatTodaysDrillKicker / formatTodaysDrillTheme ────────────
    //
    // Pure formatters for the new TodaysDrillCard.  Day 0 displays
    // as "Day 1" because the user thinks 1-indexed; the wire field
    // is the spaced-repetition step (0 / 3 / 7) — that mapping is
    // pinned below so a future contributor adding a 4th day must
    // touch this test.

    @Test
    fun `TODAYS_DRILL_KICKER_DAY0 - day-0 reads as Day 1 of 3`() {
        assertEquals(
            "Today's drill · Day 1 of 3",
            HomeActivity.formatTodaysDrillKicker(dayOffset = 0, totalDays = 3),
        )
    }

    @Test
    fun `TODAYS_DRILL_KICKER_DAY3 - day-3 reads as Day 2 of 3`() {
        assertEquals(
            "Today's drill · Day 2 of 3",
            HomeActivity.formatTodaysDrillKicker(dayOffset = 3, totalDays = 3),
        )
    }

    @Test
    fun `TODAYS_DRILL_KICKER_DAY7 - day-7 reads as Day 3 of 3`() {
        assertEquals(
            "Today's drill · Day 3 of 3",
            HomeActivity.formatTodaysDrillKicker(dayOffset = 7, totalDays = 3),
        )
    }

    @Test
    fun `TODAYS_DRILL_THEME_GENERIC - generic collapses to bare Practice`() {
        // No "Practice · Generic" — that reads as filler copy.
        assertEquals("Practice", HomeActivity.formatTodaysDrillTheme("generic"))
        assertEquals("Practice", HomeActivity.formatTodaysDrillTheme(""))
    }

    @Test
    fun `TODAYS_DRILL_THEME_SNAKE_CASE - snake_case becomes sentence case`() {
        assertEquals(
            "Practice · King safety",
            HomeActivity.formatTodaysDrillTheme("king_safety"),
        )
        assertEquals(
            "Practice · Back rank",
            HomeActivity.formatTodaysDrillTheme("back_rank"),
        )
        assertEquals(
            "Practice · Hung piece",
            HomeActivity.formatTodaysDrillTheme("hung_piece"),
        )
        assertEquals(
            "Practice · Opening principles",
            HomeActivity.formatTodaysDrillTheme("opening_principles"),
        )
    }

    @Test
    fun `TODAYS_DRILL_THEME_SINGLE_WORD - single word capitalises first letter`() {
        assertEquals("Practice · Fork", HomeActivity.formatTodaysDrillTheme("fork"))
        assertEquals("Practice · Pin", HomeActivity.formatTodaysDrillTheme("pin"))
        assertEquals("Practice · Tempo", HomeActivity.formatTodaysDrillTheme("tempo"))
    }
}
