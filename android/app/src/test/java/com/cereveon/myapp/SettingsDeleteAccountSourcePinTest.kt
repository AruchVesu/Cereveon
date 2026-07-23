package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Bidirectional source-pins for the Settings › Account › Delete account
 * flow (GDPR Art. 17; DELETE /auth/me, docs/API_CONTRACTS.md §41).
 *
 * Same XML↔Kotlin drift-guard idiom as GamePanelActionsSourcePinTest —
 * the wirings live in view-layer code that host-JVM tests can't
 * instantiate, so the pins read the source directly.  The wire contract
 * of the client call itself is covered by AuthDeleteAccountIntegrationTest.
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_DELETE_ROW     bottom_sheet_settings.xml declares exactly
 *                                 one @+id/rowDeleteAccount.
 *  2. SHEET_ROW_ROUTES_CALLBACK   the row's listener dismisses the sheet and
 *                                 invokes onDeleteAccountTapped — never the
 *                                 network call itself.
 *  3. MAIN_HOST_WIRES_SHARED_FLOW MainActivity wires onDeleteAccountTapped to
 *                                 AccountFlows.confirmAndDeleteAccount.
 *  4. HOME_HOST_WIRES_SHARED_FLOW HomeActivity wires the SAME shared flow, so
 *                                 the two hosts cannot drift.
 *  5. DELETE_ASKS_ARE_YOU_SURE    delete_account_confirm_message copy
 *                                 literally asks "Are you sure" (same product
 *                                 rule as the Reset confirmation).
 *  6. TAP_IS_NOT_DESTRUCTIVE      confirmAndDeleteAccount only shows the
 *                                 dialog; deleteAccount( is unreachable from
 *                                 it except via the positive button's
 *                                 performAccountDeletion.
 *  7. CONFIRM_RUNS_DELETE         performAccountDeletion actually calls
 *                                 authApiClient.deleteAccount(token).
 *  8. FAILURE_KEEPS_LOCAL_STATE   clearLocalUserState appears exactly once in
 *                                 AccountFlows — inside the Success branch —
 *                                 so a failed deletion never wipes the device.
 */
class SettingsDeleteAccountSourcePinTest {

    private val layoutPath = "src/main/res/layout/bottom_sheet_settings.xml"
    private val sheetPath = "src/main/java/com/cereveon/myapp/SettingsBottomSheet.kt"
    private val mainPath = "src/main/java/com/cereveon/myapp/MainActivity.kt"
    private val homePath = "src/main/java/com/cereveon/myapp/HomeActivity.kt"
    private val flowsPath = "src/main/java/com/cereveon/myapp/AccountFlows.kt"
    private val stringsPath = "src/main/res/values/strings.xml"

    private fun sourceBetween(
        source: String,
        path: String,
        fromAnchor: String,
        toAnchor: String,
    ): String {
        val start = source.indexOf(fromAnchor)
        assertTrue("anchor '$fromAnchor' not found in $path — pin needs updating", start >= 0)
        val end = source.indexOf(toAnchor, start)
        assertTrue("anchor '$toAnchor' not found after '$fromAnchor' in $path", end > start)
        return source.substring(start, end)
    }

    @Test
    fun `XML_DECLARES_DELETE_ROW - settings sheet has exactly one rowDeleteAccount`() {
        val xml = File(layoutPath).readText()
        assertEquals(
            "Expected exactly one @+id/rowDeleteAccount in $layoutPath.  If the row " +
                "moved surfaces, update this pin AND SettingsBottomSheet's wire-up.",
            1,
            Regex("""android:id\s*=\s*"@\+id/rowDeleteAccount"""").findAll(xml).count(),
        )
    }

    @Test
    fun `SHEET_ROW_ROUTES_CALLBACK - row dismisses then invokes the host callback`() {
        val kt = File(sheetPath).readText()
        val pattern = Regex(
            """R\.id\.rowDeleteAccount\)\.setOnClickListener\s*\{\s*dismiss\(\)\s*onDeleteAccountTapped\?\.invoke\(\)""",
        )
        assertTrue(
            "SettingsBottomSheet must wire rowDeleteAccount to dismiss() + " +
                "onDeleteAccountTapped — the sheet itself never performs the deletion.",
            pattern.containsMatchIn(kt),
        )
        assertFalse(
            "SettingsBottomSheet must not call deleteAccount( — the flow (dialog + " +
                "network + routing) belongs to AccountFlows via the host callback.",
            kt.contains("deleteAccount("),
        )
    }

    @Test
    fun `MAIN_HOST_WIRES_SHARED_FLOW - MainActivity routes to AccountFlows`() {
        val kt = File(mainPath).readText()
        val pattern = Regex(
            """onDeleteAccountTapped\s*=\s*\{[^}]*AccountFlows\.confirmAndDeleteAccount""",
        )
        assertTrue(
            "MainActivity must wire onDeleteAccountTapped to " +
                "AccountFlows.confirmAndDeleteAccount — the shared confirmation-gated flow.",
            pattern.containsMatchIn(kt),
        )
    }

    @Test
    fun `HOME_HOST_WIRES_SHARED_FLOW - HomeActivity routes to AccountFlows`() {
        val kt = File(homePath).readText()
        val pattern = Regex(
            """onDeleteAccountTapped\s*=\s*\{[^}]*AccountFlows\.confirmAndDeleteAccount""",
        )
        assertTrue(
            "HomeActivity must wire onDeleteAccountTapped to " +
                "AccountFlows.confirmAndDeleteAccount — same flow as MainActivity, no drift.",
            pattern.containsMatchIn(kt),
        )
    }

    @Test
    fun `DELETE_ASKS_ARE_YOU_SURE - confirmation copy literally asks Are you sure`() {
        val strings = File(stringsPath).readText()
        val message = Regex("""<string name="delete_account_confirm_message">([^<]*)</string>""")
            .find(strings)?.groupValues?.get(1)
        assertTrue(
            "strings.xml must declare delete_account_confirm_message and its copy must " +
                "ask \"Are you sure\" — same product rule as reset_confirm_message, " +
                "found: $message",
            message != null && message.contains("Are you sure"),
        )
    }

    @Test
    fun `TAP_IS_NOT_DESTRUCTIVE - confirm dialog gates the deletion`() {
        val flows = File(flowsPath).readText()
        val confirmBlock = sourceBetween(
            flows,
            flowsPath,
            "fun confirmAndDeleteAccount",
            "private fun performAccountDeletion",
        )
        assertFalse(
            "confirmAndDeleteAccount must NOT call deleteAccount( directly — the " +
                "destructive call belongs in performAccountDeletion, reachable only " +
                "from the dialog's positive button.",
            confirmBlock.contains("deleteAccount("),
        )
        assertTrue(
            "confirmAndDeleteAccount's positive button must invoke performAccountDeletion.",
            Regex("""setPositiveButton\(R\.string\.delete_account_confirm_positive\)[\s\S]*?performAccountDeletion\(""")
                .containsMatchIn(confirmBlock),
        )
        assertTrue(
            "confirmAndDeleteAccount must title the dialog with " +
                "delete_account_confirm_title.",
            confirmBlock.contains("R.string.delete_account_confirm_title"),
        )
    }

    @Test
    fun `CONFIRM_RUNS_DELETE - performAccountDeletion calls the client`() {
        val flows = File(flowsPath).readText()
        val body = sourceBetween(
            flows,
            flowsPath,
            "private fun performAccountDeletion",
            "private fun clearLocalUserState",
        )
        assertTrue(
            "performAccountDeletion must call authApiClient.deleteAccount(token) — " +
                "otherwise confirming the dialog silently does nothing.",
            body.contains("authApiClient.deleteAccount(token)"),
        )
    }

    @Test
    fun `FAILURE_KEEPS_LOCAL_STATE - only the Success branch wipes the device`() {
        val flows = File(flowsPath).readText()
        val calls = Regex("""clearLocalUserState\(""").findAll(flows).count()
        assertEquals(
            "clearLocalUserState must be invoked from exactly ONE call site (the " +
                "Success branch of performAccountDeletion) plus its own definition — " +
                "a failed deletion must never wipe local state.",
            2, // one definition + one call site
            calls,
        )
        val body = sourceBetween(
            flows,
            flowsPath,
            "private fun performAccountDeletion",
            "private fun clearLocalUserState",
        )
        val successBranch = sourceBetween(
            body,
            flowsPath,
            "is ApiResult.Success",
            "is ApiResult.HttpError",
        )
        assertTrue(
            "The Success branch must clear local user state before routing to login.",
            successBranch.contains("clearLocalUserState("),
        )
    }
}
