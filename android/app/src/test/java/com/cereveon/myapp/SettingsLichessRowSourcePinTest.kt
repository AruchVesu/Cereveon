package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Source-pins for the Settings › Integrations › Lichess row's status
 * label (2026-07-19 fix).
 *
 * The row's trailing value used to be a STATIC ``"Not linked"`` literal in
 * the layout that no code ever updated — so a linked account still read
 * "Not linked" in Settings even though the authoritative
 * [LichessConnectBottomSheet] (reading the same GET /lichess/status
 * endpoint) showed "Linked".  These pins guard the fix: the value is a
 * string resource, and [SettingsBottomSheet] populates it from the live
 * status.  Same XML↔Kotlin drift-guard idiom as
 * [SettingsDownloadDataSourcePinTest].
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_VALUE_ROW    bottom_sheet_settings.xml declares exactly one
 *                               @+id/rowConnectLichessValue.
 *  2. XML_VALUE_NOT_HARDCODED   its default text is a @string resource, and the
 *                               layout carries NO hardcoded "Not linked" literal
 *                               (that frozen literal WAS the bug).
 *  3. SHEET_FETCHES_STATUS      onViewCreated passes rowConnectLichessValue to
 *                               refreshLichessRow, which reads GET /lichess/status
 *                               off the main thread.
 *  4. ROW_REFLECTS_LINK_STATE   the helper drives the value from status.linked /
 *                               externalUsername (the linked handle when linked).
 */
class SettingsLichessRowSourcePinTest {

    private val layoutPath = "src/main/res/layout/bottom_sheet_settings.xml"
    private val sheetPath = "src/main/java/com/cereveon/myapp/SettingsBottomSheet.kt"

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
    fun `XML_DECLARES_VALUE_ROW - settings sheet has exactly one rowConnectLichessValue`() {
        val xml = File(layoutPath).readText()
        assertEquals(
            "Expected exactly one @+id/rowConnectLichessValue in $layoutPath.",
            1,
            Regex("""android:id\s*=\s*"@\+id/rowConnectLichessValue"""").findAll(xml).count(),
        )
    }

    @Test
    fun `XML_VALUE_NOT_HARDCODED - the row value is a string resource, not a static literal`() {
        val xml = File(layoutPath).readText()
        assertTrue(
            "rowConnectLichessValue must default its text to " +
                "@string/lichess_settings_not_linked (a resource), so the row is " +
                "populated from real status rather than a frozen label.",
            Regex(
                """@\+id/rowConnectLichessValue[\s\S]*?android:text="@string/lichess_settings_not_linked"""",
            ).containsMatchIn(xml),
        )
        assertFalse(
            "The settings layout must carry NO hardcoded \"Not linked\" android:text — " +
                "that static literal was the bug (the row never reflected a linked account).",
            xml.contains("android:text=\"Not linked\""),
        )
    }

    @Test
    fun `SHEET_FETCHES_STATUS - onViewCreated refreshes the row from GET lichess status`() {
        val kt = File(sheetPath).readText()
        assertTrue(
            "onViewCreated must pass rowConnectLichessValue to refreshLichessRow.",
            Regex(
                """refreshLichessRow\(\s*view\.findViewById\(R\.id\.rowConnectLichessValue\)\s*\)""",
            ).containsMatchIn(kt),
        )
        val helper = sourceBetween(
            kt,
            sheetPath,
            "private fun refreshLichessRow",
            "private fun showEditRatingDialog",
        )
        assertTrue(
            "refreshLichessRow must read GET /lichess/status via LichessApiClient.status.",
            helper.contains("client.status("),
        )
        assertTrue(
            "refreshLichessRow must run the network call off the main thread.",
            helper.contains("Dispatchers.IO"),
        )
    }

    @Test
    fun `ROW_REFLECTS_LINK_STATE - value is driven by the live linked flag and handle`() {
        val kt = File(sheetPath).readText()
        val helper = sourceBetween(
            kt,
            sheetPath,
            "private fun refreshLichessRow",
            "private fun showEditRatingDialog",
        )
        assertTrue(
            "refreshLichessRow must branch on status.linked.",
            helper.contains("status.linked"),
        )
        assertTrue(
            "refreshLichessRow must show the linked handle via " +
                "R.string.lichess_settings_linked_as when linked.",
            helper.contains("R.string.lichess_settings_linked_as"),
        )
        assertTrue(
            "refreshLichessRow must assign the row's text (valueView.text).",
            helper.contains("valueView.text ="),
        )
    }
}
