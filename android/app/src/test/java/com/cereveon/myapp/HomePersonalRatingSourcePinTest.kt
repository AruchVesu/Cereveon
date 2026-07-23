package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Bidirectional source-pin for the player-anchor kicker on the Home
 * screen.
 *
 * History
 * -------
 * Originally added in PR #184 as the personal-rating kicker (closing
 * 2026-05-18 user feedback "When I open the app there is no personal
 * rating - it appears only after a game").  When Elo was hidden from
 * the UI the same view-id was repurposed to display training XP
 * (``Level N · X XP``) — the layout id, view-type cast, and the
 * cache-then-refresh wiring all carry over; only the cached
 * SharedPreferences key changed from ``PREF_RATING`` to
 * ``PREF_TRAINING_XP``.  The pin still guards against drift between
 * the XML and Kotlin sides, which would crash cold-start with a
 * ClassCastException (see GameSummaryTrainingCardSourcePinTest for
 * the original incident pattern).
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_HOME_PERSONAL_RATING   activity_home.xml declares
 *                                          exactly one element with
 *                                          android:id=@+id/homePersonalRating.
 *  2. KOTLIN_FINDS_HOME_PERSONAL_RATING    HomeActivity.kt looks up
 *                                          the view via findViewById.
 *  3. CAST_MATCHES_VIEW_TYPE               XML tag short-name == Kotlin
 *                                          cast short-name.
 *  4. HOME_FETCHES_AUTH_ME                 HomeActivity calls
 *                                          authApiClient.me on cold-
 *                                          start so the kicker
 *                                          populates without waiting
 *                                          for the next game finish.
 *  5. HOME_REREADS_XP_IN_ON_RESUME         HomeActivity.onResume re-
 *                                          reads PREF_TRAINING_XP so a
 *                                          training completed in
 *                                          MainActivity updates the
 *                                          kicker without requiring a
 *                                          cold-start.
 */
class HomePersonalRatingSourcePinTest {

    private val xmlPath = "src/main/res/layout/activity_home.xml"
    private val ktPath  = "src/main/java/com/cereveon/myapp/HomeActivity.kt"

    private val viewId = "homePersonalRating"

    @Test
    fun `XML_DECLARES_HOME_PERSONAL_RATING - layout has exactly one homePersonalRating element`() {
        val xml = File(xmlPath).readText()
        val occurrences = Regex("""android:id\s*=\s*"@\+id/$viewId"""").findAll(xml).count()
        assertEquals(
            "Expected exactly one <... android:id=\"@+id/$viewId\"> in $xmlPath, " +
                "found $occurrences.  If you intentionally split the kicker, " +
                "update this pin AND HomeActivity's wire-up.",
            1, occurrences,
        )
    }

    @Test
    fun `KOTLIN_FINDS_HOME_PERSONAL_RATING - HomeActivity looks up the view via findViewById`() {
        val kt = File(ktPath).readText()
        val pattern = Regex("""findViewById<[\w.]+>\s*\(\s*R\.id\.$viewId\s*\)""")
        assertTrue(
            "HomeActivity.kt does not call findViewById<...>(R.id.$viewId).  " +
                "Either drop the XML view (and this pin) or wire the Kotlin reader.",
            pattern.containsMatchIn(kt),
        )
    }

    @Test
    fun `CAST_MATCHES_VIEW_TYPE - Kotlin cast short name matches XML tag short name`() {
        val xml = File(xmlPath).readText()
        val kt  = File(ktPath).readText()

        val xmlTag = Regex(
            """<\s*(?<tag>[\w.]+)\b[^>]*?android:id\s*=\s*"@\+id/$viewId"""",
            RegexOption.DOT_MATCHES_ALL,
        ).find(xml)
            ?.groups
            ?.get("tag")
            ?.value
            ?: error("XML missing element with android:id=@+id/$viewId")

        val ktCast = Regex(
            """findViewById<\s*(?<cast>[\w.]+)\s*>\s*\(\s*R\.id\.$viewId\s*\)""",
        ).find(kt)
            ?.groups
            ?.get("cast")
            ?.value
            ?: error("Kotlin missing findViewById<...>(R.id.$viewId)")

        val xmlShort = xmlTag.substringAfterLast('.')
        val ktShort  = ktCast.substringAfterLast('.')
        assertEquals(
            "$viewId view-type mismatch: XML declares <$xmlTag> but Kotlin " +
                "casts to <$ktCast>.  Crash at runtime with ClassCastException " +
                "if these drift — see GameSummaryTrainingCardSourcePinTest for " +
                "the original incident.",
            xmlShort, ktShort,
        )
    }

    @Test
    fun `HOME_FETCHES_AUTH_ME - HomeActivity calls authApiClient_me on cold-start`() {
        val kt = File(ktPath).readText()
        assertTrue(
            "HomeActivity.kt must call ``authApiClient.me(...)`` somewhere — " +
                "otherwise a fresh install (no cached PREF_TRAINING_XP) leaves " +
                "the kicker empty until the user opens MainActivity or " +
                "finishes a game.  Same cold-start guarantee that PR #184 " +
                "introduced for the rating kicker; carried over to the XP " +
                "kicker that replaced it.",
            kt.contains("authApiClient.me("),
        )
    }

    @Test
    fun `HOME_REREADS_XP_IN_ON_RESUME - onResume refreshes the kicker from PREF_TRAINING_XP`() {
        val kt = File(ktPath).readText()
        // Slice onResume's body so the match doesn't accidentally
        // pick up the onCreate read (which DOES exist but isn't what
        // this invariant pins).
        val onResumeStart = kt.indexOf("override fun onResume()")
        assertTrue(
            "onResume override not found in HomeActivity.kt — has the " +
                "Activity lifecycle hook been renamed or removed?",
            onResumeStart >= 0,
        )
        // The function body extends to the matching closing brace; a
        // cheap upper-bound is the next ``override fun`` declaration
        // OR the end of the file.
        val nextOverride = kt.indexOf("override fun", onResumeStart + 20)
        val onResumeBody = if (nextOverride >= 0)
            kt.substring(onResumeStart, nextOverride)
        else
            kt.substring(onResumeStart)

        assertTrue(
            "HomeActivity.onResume must re-read ``MainActivity.PREF_TRAINING_XP`` " +
                "from SharedPreferences so a training completed in MainActivity " +
                "updates the kicker when the user pops back to Home.  " +
                "Without this hook, the kicker stays at the stale onCreate " +
                "value until the next cold-start.",
            onResumeBody.contains("PREF_TRAINING_XP"),
        )
    }
}
