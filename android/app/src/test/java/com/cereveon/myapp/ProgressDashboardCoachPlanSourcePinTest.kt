package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Bidirectional source-pin for the "Coach's plan" section added to
 * the Progress Dashboard bottom sheet (PR #172).
 *
 * Why this test exists
 * --------------------
 * On 2026-05-15, the same kind of drift between
 * ``bottom_sheet_game_summary.xml`` and ``GameSummaryBottomSheet.kt``
 * crashed the post-game summary on a ``ClassCastException`` —
 * see [[GameSummaryTrainingCardSourcePinTest]] for the template.
 * The new "Coach's plan" section in
 * ``bottom_sheet_progress_dashboard.xml`` adds 8 view IDs that are
 * looked up by ``ProgressDashboardBottomSheet`` via ``findViewById``;
 * a future Atrium re-skin that changes any of those view classes
 * without updating the Kotlin cast would crash the dashboard the
 * same way.
 *
 * Each XML element with an id is pinned to the Kotlin cast that
 * reads it.  XML uses the fully-qualified class name for custom
 * views (``ai.chesscoach.app.AtriumCardView``) and the bare class
 * name for ``android.widget`` / ``android.view`` classes; the
 * Kotlin side always uses the bare name.  Comparison is by short
 * (after-last-dot) class name.
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_COACH_PLAN_IDS    every coach-plan view ID
 *                                    appears exactly once in
 *                                    bottom_sheet_progress_dashboard.xml.
 *  2. KOTLIN_FINDS_COACH_PLAN_IDS    every coach-plan view ID is
 *                                    fetched via findViewById in
 *                                    ProgressDashboardBottomSheet.kt.
 *  3. CASTS_MATCH_VIEW_TYPES         for each ID, the Kotlin cast
 *                                    short name matches the XML
 *                                    tag short name.
 */
class ProgressDashboardCoachPlanSourcePinTest {

    private val xmlPath = "src/main/res/layout/bottom_sheet_progress_dashboard.xml"
    private val ktPath  = "src/main/java/com/cereveon/myapp/ProgressDashboardBottomSheet.kt"

    /**
     * The eight view IDs the PR #172 "Coach's plan" section
     * introduced.  Listed here as a single source-of-truth so the
     * parametrised assertions and the completeness check (no view
     * left unchecked) share one definition.
     */
    private val coachPlanIds: List<String> = listOf(
        "txtCoachPlanKicker",
        "coachPlanCard",
        "coachPlanDivider",
        "txtCoachPlanAction",
        "txtCoachPlanWeakness",
        "txtCoachPlanTitle",
        "txtCoachPlanDescription",
        "txtCoachPlanReason",
    )

    private fun xmlTagFor(id: String, xml: String): String {
        val regex = Regex(
            """<\s*(?<tag>[\w.]+)\b[^>]*?android:id\s*=\s*"@\+id/$id"""",
            RegexOption.DOT_MATCHES_ALL,
        )
        val match = regex.find(xml)
            ?: error("XML missing element with android:id=\"@+id/$id\"")
        return match.groups["tag"]!!.value
    }

    private fun ktCastFor(id: String, kt: String): String {
        val regex = Regex(
            """findViewById<\s*(?<cast>[\w.]+)\s*>\s*\(\s*R\.id\.$id\s*\)""",
        )
        val match = regex.find(kt)
            ?: error(
                "Kotlin missing findViewById<...>(R.id.$id) in ProgressDashboardBottomSheet.kt. " +
                    "If the view was retired, drop the ID from this test."
            )
        return match.groups["cast"]!!.value
    }

    @Test
    fun `XML_DECLARES_COACH_PLAN_IDS - each ID appears exactly once`() {
        val xml = File(xmlPath).readText()
        for (id in coachPlanIds) {
            val regex = Regex("""android:id\s*=\s*"@\+id/$id"""")
            val occurrences = regex.findAll(xml).count()
            assertEquals(
                "Expected exactly one XML element with android:id=\"@+id/$id\", " +
                    "found $occurrences.  If you intentionally duplicated, drop " +
                    "the ID from this pin and explain why.",
                1,
                occurrences,
            )
        }
    }

    @Test
    fun `KOTLIN_FINDS_COACH_PLAN_IDS - each ID is fetched in Kotlin`() {
        val kt = File(ktPath).readText()
        for (id in coachPlanIds) {
            val regex = Regex("""findViewById<[\w.]+>\s*\(\s*R\.id\.$id\s*\)""")
            assertTrue(
                "ProgressDashboardBottomSheet.kt does not call " +
                    "findViewById<...>(R.id.$id).  Either drop the XML view (and " +
                    "this pin row) or wire the Kotlin reader.",
                regex.containsMatchIn(kt),
            )
        }
    }

    @Test
    fun `CASTS_MATCH_VIEW_TYPES - Kotlin cast short name matches XML tag short name`() {
        val xml = File(xmlPath).readText()
        val kt  = File(ktPath).readText()

        for (id in coachPlanIds) {
            val xmlTag  = xmlTagFor(id, xml)
            val ktCast  = ktCastFor(id, kt)
            val xmlShort = xmlTag.substringAfterLast('.')
            val ktShort  = ktCast.substringAfterLast('.')
            assertEquals(
                "View-type mismatch for R.id.$id: XML declares <$xmlTag> " +
                    "but Kotlin casts to <$ktCast>.  This is the exact class " +
                    "of bug that crashed the post-game summary on 2026-05-15 " +
                    "(see GameSummaryTrainingCardSourcePinTest).",
                xmlShort,
                ktShort,
            )
        }
    }
}
