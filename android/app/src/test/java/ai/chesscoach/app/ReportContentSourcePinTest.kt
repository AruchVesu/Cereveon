package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Source-pins for the in-app "Report AI content" affordance (Google Play
 * AI-Generated Content policy; POST /coach/report, docs/API_CONTRACTS.md
 * §45).  The wiring lives in view-layer code host-JVM tests can't
 * instantiate, so — same idiom as the other source-pin tests — these
 * read the source directly.  The wire contract is covered by
 * CoachReportContentTest.
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_REPORT       item_chat_coach.xml declares exactly one
 *                               @+id/btnReport.
 *  2. ADAPTER_EXPOSES_REPORT    ChatAdapter has an onReport callback and
 *                               wires btnReport's click to it.
 *  3. SHEET_WIRES_DIALOG        ChatBottomSheet routes onReport to
 *                               showReportDialog (the tap never reports).
 *  4. DIALOG_POSTS_REPORT       showReportDialog posts via
 *                               coachApiClient.reportContent from the
 *                               positive button.
 *  5. STRINGS_PRESENT           the report dialog strings exist.
 */
class ReportContentSourcePinTest {

    private val layoutPath = "src/main/res/layout/item_chat_coach.xml"
    private val adapterPath = "src/main/java/ai/chesscoach/app/ChatAdapter.kt"
    private val sheetPath = "src/main/java/ai/chesscoach/app/ChatBottomSheet.kt"
    private val stringsPath = "src/main/res/values/strings.xml"

    private fun sourceBetween(src: String, from: String, to: String): String {
        val start = src.indexOf(from)
        assertTrue("anchor '$from' not found", start >= 0)
        val end = src.indexOf(to, start + from.length)
        assertTrue("anchor '$to' not found after '$from'", end > start)
        return src.substring(start, end)
    }

    @Test
    fun `XML_DECLARES_REPORT - coach row has exactly one btnReport`() {
        val xml = File(layoutPath).readText()
        assertEquals(
            "Expected exactly one @+id/btnReport in $layoutPath.",
            1,
            Regex("""android:id\s*=\s*"@\+id/btnReport"""").findAll(xml).count(),
        )
    }

    @Test
    fun `ADAPTER_EXPOSES_REPORT - onReport callback wired to btnReport`() {
        val kt = File(adapterPath).readText()
        assertTrue(
            "ChatAdapter must declare an onReport callback.",
            Regex("""var\s+onReport\s*:""").containsMatchIn(kt),
        )
        assertTrue(
            "ChatAdapter must wire btnReport's click to onReport (via the CoachVH 'report' view).",
            kt.contains("holder.report.setOnClickListener") &&
                Regex("""onReport\?\.invoke""").containsMatchIn(kt),
        )
    }

    @Test
    fun `SHEET_WIRES_DIALOG - onReport opens the dialog, never reports directly`() {
        val kt = File(sheetPath).readText()
        val wiring = sourceBetween(kt, "chatAdapter.onReport =", "}")
        assertTrue(
            "ChatBottomSheet must route onReport to showReportDialog.",
            wiring.contains("showReportDialog"),
        )
        assertFalse(
            "The onReport tap wiring must NOT call reportContent directly — the " +
                "network call belongs in showReportDialog's positive button.",
            wiring.contains("reportContent"),
        )
    }

    @Test
    fun `DIALOG_POSTS_REPORT - showReportDialog posts via reportContent`() {
        val kt = File(sheetPath).readText()
        val dialog = sourceBetween(kt, "private fun showReportDialog", "private fun appendUser")
        assertTrue(
            "showReportDialog must call coachApiClient.reportContent(.",
            dialog.contains("coachApiClient.reportContent("),
        )
        assertTrue(
            "showReportDialog must gate the report behind a positive button.",
            dialog.contains("setPositiveButton(R.string.report_dialog_positive)"),
        )
        assertTrue(
            "the report must be tagged with the chat surface.",
            Regex("""surface\s*=\s*"chat"""").containsMatchIn(dialog),
        )
    }

    @Test
    fun `STRINGS_PRESENT - report dialog strings declared`() {
        val strings = File(stringsPath).readText()
        for (name in listOf("report_dialog_title", "report_dialog_positive", "report_dialog_message")) {
            assertTrue(
                "strings.xml must declare $name.",
                strings.contains("""<string name="$name">"""),
            )
        }
    }
}
