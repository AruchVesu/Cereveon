package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Bidirectional source-pins for the game drawer's account/app actions
 * (2026-07-10 product request): the reinstated standalone Sign out
 * button, the Send feedback form entry, and the "Are you sure"
 * confirmation now gating Reset game.
 *
 * Same XML↔Kotlin drift-guard idiom as HomePersonalRatingSourcePinTest
 * — these wirings live in view-layer code that host-JVM tests can't
 * instantiate (MainActivity loads the native engine + redirects on
 * auth), so the pins read the source directly.  A failure message
 * always names both sides that must move together.
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_SIGN_OUT          activity_main.xml declares exactly one
 *                                    @+id/btnSignOut.
 *  2. XML_DECLARES_SEND_FEEDBACK     activity_main.xml declares exactly one
 *                                    @+id/btnSendFeedback.
 *  3. SIGN_OUT_ROUTES_SHARED_FLOW    btnSignOut's listener calls
 *                                    AccountFlows.performLogout — the SAME
 *                                    flow as the Settings sheet row, so the
 *                                    two surfaces cannot drift.
 *  4. SEND_FEEDBACK_OPENS_FORM       btnSendFeedback's listener calls
 *                                    FeedbackFlows.showSendFeedbackDialog.
 *  5. RESET_ASKS_ARE_YOU_SURE        reset_confirm_message copy literally
 *                                    asks "Are you sure" (the product
 *                                    requirement, verbatim).
 *  6. RESET_TAP_IS_NOT_DESTRUCTIVE   btnReset's click listener contains NO
 *                                    destructive call (viewModel.reset /
 *                                    startNewGameSession) — only the
 *                                    confirmation dialog whose positive
 *                                    button invokes performResetGame().
 *  7. RESET_CONFIRM_RUNS_FULL_BODY   performResetGame() carries the
 *                                    destructive body (viewModel.reset +
 *                                    startNewGameSession) so confirming
 *                                    actually resets.
 *  8. FEEDBACK_FORM_POSTS_VERSION    FeedbackFlows submits via
 *                                    submitFeedback with
 *                                    BuildConfig.VERSION_NAME attached.
 */
class GamePanelActionsSourcePinTest {

    private val xmlPath = "src/main/res/layout/activity_main.xml"
    private val ktPath = "src/main/java/ai/chesscoach/app/MainActivity.kt"
    private val flowsPath = "src/main/java/ai/chesscoach/app/FeedbackFlows.kt"
    private val stringsPath = "src/main/res/values/strings.xml"

    private fun countIdDeclarations(xml: String, viewId: String): Int =
        Regex("""android:id\s*=\s*"@\+id/$viewId"""").findAll(xml).count()

    /**
     * The source of [ktPath] between the first occurrence of [fromAnchor]
     * and the next occurrence of [toAnchor] — used to scope assertions to
     * one listener block.  Fails the test loudly if either anchor is gone
     * (that in itself is drift worth flagging).
     */
    private fun sourceBetween(source: String, fromAnchor: String, toAnchor: String): String {
        val start = source.indexOf(fromAnchor)
        assertTrue("anchor '$fromAnchor' not found in $ktPath — pin needs updating", start >= 0)
        val end = source.indexOf(toAnchor, start)
        assertTrue("anchor '$toAnchor' not found after '$fromAnchor' in $ktPath", end > start)
        return source.substring(start, end)
    }

    @Test
    fun `XML_DECLARES_SIGN_OUT - drawer has exactly one btnSignOut`() {
        val xml = File(xmlPath).readText()
        assertEquals(
            "Expected exactly one @+id/btnSignOut in $xmlPath.  If the button " +
                "moved surfaces, update this pin AND MainActivity's wire-up.",
            1, countIdDeclarations(xml, "btnSignOut"),
        )
    }

    @Test
    fun `XML_DECLARES_SEND_FEEDBACK - drawer has exactly one btnSendFeedback`() {
        val xml = File(xmlPath).readText()
        assertEquals(
            "Expected exactly one @+id/btnSendFeedback in $xmlPath.  If the button " +
                "moved surfaces, update this pin AND MainActivity's wire-up.",
            1, countIdDeclarations(xml, "btnSendFeedback"),
        )
    }

    @Test
    fun `SIGN_OUT_ROUTES_SHARED_FLOW - btnSignOut listener calls AccountFlows performLogout`() {
        val kt = File(ktPath).readText()
        val pattern = Regex(
            """R\.id\.btnSignOut\)\?*\.setOnClickListener\s*\{[^}]*AccountFlows\.performLogout""",
        )
        assertTrue(
            "MainActivity.kt must wire btnSignOut to AccountFlows.performLogout — the " +
                "shared flow the Settings sheet's Account row also uses.  A different " +
                "logout path here would let the two surfaces drift.",
            pattern.containsMatchIn(kt),
        )
    }

    @Test
    fun `SEND_FEEDBACK_OPENS_FORM - btnSendFeedback listener opens FeedbackFlows dialog`() {
        val kt = File(ktPath).readText()
        val pattern = Regex(
            """R\.id\.btnSendFeedback\)\?*\.setOnClickListener\s*\{[^}]*FeedbackFlows\.showSendFeedbackDialog""",
        )
        assertTrue(
            "MainActivity.kt must wire btnSendFeedback to " +
                "FeedbackFlows.showSendFeedbackDialog (the /feedback form).",
            pattern.containsMatchIn(kt),
        )
    }

    @Test
    fun `RESET_ASKS_ARE_YOU_SURE - confirmation copy literally asks Are you sure`() {
        val strings = File(stringsPath).readText()
        val message = Regex("""<string name="reset_confirm_message">([^<]*)</string>""")
            .find(strings)?.groupValues?.get(1)
        assertTrue(
            "strings.xml must declare reset_confirm_message and its copy must ask " +
                "\"Are you sure\" — that phrasing is the product requirement, " +
                "found: $message",
            message != null && message.contains("Are you sure"),
        )
    }

    @Test
    fun `RESET_TAP_IS_NOT_DESTRUCTIVE - btnReset listener only shows the confirm dialog`() {
        val kt = File(ktPath).readText()
        val listenerBlock = sourceBetween(kt, "btnReset.setOnClickListener", "btnUndo.setOnClickListener")

        assertFalse(
            "btnReset's click listener must NOT call viewModel.reset() directly — " +
                "the destructive body belongs in performResetGame(), reachable only " +
                "from the confirmation dialog's positive button.",
            listenerBlock.contains("viewModel.reset()"),
        )
        assertFalse(
            "btnReset's click listener must NOT call startNewGameSession() directly — " +
                "a declined confirmation must leave the current game untouched.",
            listenerBlock.contains("startNewGameSession"),
        )
        assertTrue(
            "btnReset's click listener must show the confirmation dialog " +
                "(reset_confirm_title) before any reset happens.",
            listenerBlock.contains("R.string.reset_confirm_title"),
        )
        assertTrue(
            "the confirmation dialog's positive button must invoke performResetGame().",
            Regex("""setPositiveButton\(R\.string\.reset_confirm_positive\)[\s\S]*?performResetGame\(\)""")
                .containsMatchIn(listenerBlock),
        )
    }

    @Test
    fun `RESET_CONFIRM_RUNS_FULL_BODY - performResetGame carries the destructive body`() {
        val kt = File(ktPath).readText()
        val body = sourceBetween(kt, "private fun performResetGame()", "private fun startNewGameSession")
        assertTrue(
            "performResetGame() must reset the ViewModel — otherwise confirming " +
                "the dialog silently does nothing.",
            body.contains("viewModel.reset()"),
        )
        assertTrue(
            "performResetGame() must start a fresh server game session — the reset " +
                "contract includes a new /game/start admission verdict.",
            body.contains("startNewGameSession()"),
        )
    }

    @Test
    fun `FEEDBACK_FORM_POSTS_VERSION - FeedbackFlows submits with the app version attached`() {
        val flows = File(flowsPath).readText()
        assertTrue(
            "FeedbackFlows must POST via FeedbackApiClient.submitFeedback.",
            flows.contains("submitFeedback("),
        )
        assertTrue(
            "FeedbackFlows must attach BuildConfig.VERSION_NAME as appVersion — " +
                "version-less feedback is much harder to act on.",
            flows.contains("BuildConfig.VERSION_NAME"),
        )
    }
}
