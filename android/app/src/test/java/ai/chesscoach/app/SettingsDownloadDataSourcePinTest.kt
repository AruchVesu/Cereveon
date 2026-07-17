package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.LocalDate

/**
 * Source-pins for the Settings › Account › Download my data flow
 * (GDPR Art. 15/20; GET /auth/me/export — docs/API_CONTRACTS.md §42),
 * plus the one pure-JVM unit this flow exposes (the suggested file
 * name).  Same XML↔Kotlin drift-guard idiom as
 * SettingsDeleteAccountSourcePinTest; the wire contract itself is
 * covered by AuthExportDataIntegrationTest.
 *
 * Pinned invariants
 * -----------------
 *  1. XML_DECLARES_DOWNLOAD_ROW   bottom_sheet_settings.xml declares exactly
 *                                 one @+id/rowDownloadData.
 *  2. SHEET_ROW_ROUTES_CALLBACK   the row's listener dismisses the sheet and
 *                                 invokes onDownloadDataTapped — never the
 *                                 network call itself.
 *  3. MAIN_HOST_WIRES_FLOW        MainActivity registers the SAF launcher AND
 *                                 wires the row to DataExportFlows.startDownload.
 *  4. HOME_HOST_WIRES_FLOW        HomeActivity does the same — no drift.
 *  5. FETCH_THEN_PICK             the file picker launches exactly once in
 *                                 startDownload, from the Success branch,
 *                                 AFTER the document is held in pendingJson —
 *                                 a failed fetch never creates a file.
 *  6. RESULT_ALWAYS_CLEARS        the save callback snapshots + clears
 *                                 pendingJson before any branching, so no
 *                                 path can leave a stale document behind.
 *  7. MIME_IS_JSON                the CreateDocument contract is registered
 *                                 with application/json.
 *  8. FILE_NAME_SHAPE             suggestedFileName is deterministic:
 *                                 cereveon-data-export-<ISO date>.json.
 */
class SettingsDownloadDataSourcePinTest {

    private val layoutPath = "src/main/res/layout/bottom_sheet_settings.xml"
    private val sheetPath = "src/main/java/ai/chesscoach/app/SettingsBottomSheet.kt"
    private val mainPath = "src/main/java/ai/chesscoach/app/MainActivity.kt"
    private val homePath = "src/main/java/ai/chesscoach/app/HomeActivity.kt"
    private val flowsPath = "src/main/java/ai/chesscoach/app/DataExportFlows.kt"

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
    fun `XML_DECLARES_DOWNLOAD_ROW - settings sheet has exactly one rowDownloadData`() {
        val xml = File(layoutPath).readText()
        assertEquals(
            "Expected exactly one @+id/rowDownloadData in $layoutPath.",
            1,
            Regex("""android:id\s*=\s*"@\+id/rowDownloadData"""").findAll(xml).count(),
        )
    }

    @Test
    fun `SHEET_ROW_ROUTES_CALLBACK - row dismisses then invokes the host callback`() {
        val kt = File(sheetPath).readText()
        val pattern = Regex(
            """R\.id\.rowDownloadData\)\.setOnClickListener\s*\{\s*dismiss\(\)\s*onDownloadDataTapped\?\.invoke\(\)""",
        )
        assertTrue(
            "SettingsBottomSheet must wire rowDownloadData to dismiss() + " +
                "onDownloadDataTapped — the sheet itself never fetches the export.",
            pattern.containsMatchIn(kt),
        )
        assertFalse(
            "SettingsBottomSheet must not call exportData( — the flow belongs to " +
                "DataExportFlows via the host callback.",
            kt.contains("exportData("),
        )
    }

    @Test
    fun `MAIN_HOST_WIRES_FLOW - MainActivity registers launcher and routes the row`() {
        val kt = File(mainPath).readText()
        assertTrue(
            "MainActivity must register the SAF save launcher via " +
                "DataExportFlows.registerSaveLauncher (property initializer).",
            kt.contains("DataExportFlows.registerSaveLauncher(this)"),
        )
        assertTrue(
            "MainActivity must wire onDownloadDataTapped to DataExportFlows.startDownload.",
            Regex("""onDownloadDataTapped\s*=\s*\{[^}]*DataExportFlows\.startDownload""")
                .containsMatchIn(kt),
        )
    }

    @Test
    fun `HOME_HOST_WIRES_FLOW - HomeActivity registers launcher and routes the row`() {
        val kt = File(homePath).readText()
        assertTrue(
            "HomeActivity must register the SAF save launcher via " +
                "DataExportFlows.registerSaveLauncher (property initializer).",
            kt.contains("DataExportFlows.registerSaveLauncher(this)"),
        )
        assertTrue(
            "HomeActivity must wire onDownloadDataTapped to DataExportFlows.startDownload.",
            Regex("""onDownloadDataTapped\s*=\s*\{[^}]*DataExportFlows\.startDownload""")
                .containsMatchIn(kt),
        )
    }

    @Test
    fun `FETCH_THEN_PICK - picker launches once, from the Success branch only`() {
        val flows = File(flowsPath).readText()
        val body = sourceBetween(flows, flowsPath, "fun startDownload", "\n}")
        assertEquals(
            "saveLauncher.launch must appear exactly once in startDownload — " +
                "a failed fetch must never open the picker (and never create a file).",
            1,
            Regex("""saveLauncher\.launch\(""").findAll(body).count(),
        )
        assertTrue(
            "The Success branch must hold the document in pendingJson BEFORE " +
                "launching the picker (fetch-then-pick).",
            Regex(
                """is ApiResult\.Success ->\s*\{\s*pendingJson = result\.data\s*saveLauncher\.launch\(""",
            ).containsMatchIn(body),
        )
    }

    @Test
    fun `RESULT_ALWAYS_CLEARS - save callback snapshots and clears pendingJson first`() {
        val flows = File(flowsPath).readText()
        val callback = sourceBetween(
            flows,
            flowsPath,
            "fun registerSaveLauncher",
            "fun startDownload",
        )
        val snapshot = callback.indexOf("val json = pendingJson")
        val clear = callback.indexOf("pendingJson = null")
        val write = callback.indexOf("openOutputStream")
        assertTrue("callback must snapshot pendingJson", snapshot >= 0)
        assertTrue(
            "callback must clear pendingJson immediately after the snapshot, " +
                "before ANY branching — no path may leave a stale document.",
            clear in (snapshot + 1) until write,
        )
    }

    @Test
    fun `MIME_IS_JSON - CreateDocument contract registered with application json`() {
        val flows = File(flowsPath).readText()
        assertTrue(
            "The SAF contract must be CreateDocument(\"application/json\").",
            flows.contains("ActivityResultContracts.CreateDocument(\"application/json\")") ||
                flows.contains(
                    "ActivityResultContracts.CreateDocument(\n            \"application/json\"",
                ),
        )
    }

    @Test
    fun `FILE_NAME_SHAPE - suggested name is cereveon-data-export-date json`() {
        assertEquals(
            "cereveon-data-export-2026-07-17.json",
            DataExportFlows.suggestedFileName(LocalDate.of(2026, 7, 17)),
        )
        assertTrue(
            DataExportFlows.suggestedFileName().startsWith(DataExportFlows.EXPORT_FILE_PREFIX),
        )
        assertTrue(DataExportFlows.suggestedFileName().endsWith(".json"))
    }
}
