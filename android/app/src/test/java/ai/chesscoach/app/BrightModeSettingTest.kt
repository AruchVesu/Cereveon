package ai.chesscoach.app

import androidx.appcompat.app.AppCompatDelegate
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element

/**
 * Bright mode contract — pins the three legs the feature stands on:
 *
 *  1.  The pref → AppCompatDelegate mapping is always a FORCED mode
 *      (never FOLLOW_SYSTEM): Atrium's palette is app-controlled by
 *      design, dark by default, bright only via the Settings switch.
 *  2.  The bright palette in values-notnight/ stays structurally in
 *      sync with the base (dark) palette: no orphan overrides, every
 *      surface/ink/hairline/accent token actually flipped, and the
 *      board-object tokens deliberately NOT overridden (the chess
 *      board keeps its dark warm-wood palette in both modes —
 *      ChessBoardView renders it from matching literals).
 *  3.  The wiring: CereveonApplication applies the persisted mode
 *      before any activity inflates, and the settings switch persists
 *      then dismisses BEFORE flipping the mode (a framework-restored
 *      sheet would lose its show-time-wired Account callbacks).
 *
 * Source-pin style follows GamePanelActionsSourcePinTest: host tests
 * read main-source files relative to the module dir.
 */
class BrightModeSettingTest {

    private val baseColorsPath = "src/main/res/values/colors.xml"
    private val brightColorsPath = "src/main/res/values-notnight/colors.xml"
    private val baseThemesPath = "src/main/res/values/themes.xml"
    private val brightThemesPath = "src/main/res/values-notnight/themes.xml"
    private val settingsLayoutPath = "src/main/res/layout/bottom_sheet_settings.xml"
    private val settingsSheetPath = "src/main/java/ai/chesscoach/app/SettingsBottomSheet.kt"
    private val applicationPath = "src/main/java/ai/chesscoach/app/CereveonApplication.kt"

    // ── 1 · pref → night-mode mapping ────────────────────────────────

    @Test
    fun `bright OFF maps to forced MODE_NIGHT_YES - dark stays the default posture`() {
        assertEquals(
            AppCompatDelegate.MODE_NIGHT_YES,
            SettingsBottomSheet.nightModeFor(brightEnabled = false),
        )
    }

    @Test
    fun `bright ON maps to forced MODE_NIGHT_NO - notnight resources selected`() {
        assertEquals(
            AppCompatDelegate.MODE_NIGHT_NO,
            SettingsBottomSheet.nightModeFor(brightEnabled = true),
        )
    }

    @Test
    fun `mapping never yields FOLLOW_SYSTEM - the system toggle must not select the palette`() {
        for (bright in listOf(false, true)) {
            assertNotEquals(
                "nightModeFor($bright) must force a mode; FOLLOW_SYSTEM would let the " +
                    "system light/dark setting pick the palette, which Atrium forbids.",
                AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM,
                SettingsBottomSheet.nightModeFor(bright),
            )
        }
    }

    @Test
    fun `bright mode pref defaults OFF and key is stable`() {
        assertEquals("setting_bright_mode", SettingsBottomSheet.PREF_BRIGHT_MODE)
        // The reader must default to false (dark) when the key is absent —
        // existing installs must keep rendering exactly as before.
        val kt = File(settingsSheetPath).readText()
        assertTrue(
            "readBrightModeEnabled must default the pref to false (dark).",
            Regex("""getBoolean\(PREF_BRIGHT_MODE,\s*false\)""").containsMatchIn(kt),
        )
    }

    // ── 2 · palette parity ───────────────────────────────────────────

    /** Tokens that MUST have a bright-mode counterpart. */
    private val requiredBrightOverrides = setOf(
        "atrium_bg_base", "atrium_bg_surface", "atrium_bg_gradient_top",
        "atrium_ink", "atrium_muted", "atrium_dim",
        "atrium_hairline", "atrium_hairline_strong",
        "atrium_accent_cyan", "atrium_accent_amber",
        "atrium_accent_cyan_55", "atrium_accent_cyan_22",
        "atrium_accent_cyan_2e", "atrium_accent_cyan_1a",
        "atrium_accent_amber_cc", "atrium_accent_amber_55",
        "atrium_text_soft",
        "atrium_severity_blunder", "atrium_severity_mistake",
        "atrium_severity_inaccuracy", "atrium_severity_good",
    )

    /** Tokens that must NOT be overridden — the board object keeps the dark palette. */
    private val boardObjectTokens = setOf(
        "atrium_board_light", "atrium_board_dark",
        "atrium_piece_white", "atrium_piece_black",
    )

    private fun colorMap(path: String): Map<String, String> {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(File(path))
        val nodes = doc.getElementsByTagName("color")
        return (0 until nodes.length).associate { i ->
            val el = nodes.item(i) as Element
            el.getAttribute("name") to el.textContent.trim()
        }
    }

    @Test
    fun `every notnight override names an existing base token - no orphans`() {
        val base = colorMap(baseColorsPath).keys
        val orphans = colorMap(brightColorsPath).keys - base
        assertTrue(
            "values-notnight/colors.xml overrides tokens that don't exist in the base " +
                "palette (typo or a removed token): $orphans",
            orphans.isEmpty(),
        )
    }

    @Test
    fun `bright palette overrides every required surface, ink, hairline and accent token`() {
        val missing = requiredBrightOverrides - colorMap(brightColorsPath).keys
        assertTrue(
            "Bright mode would render these tokens with their DARK values: $missing",
            missing.isEmpty(),
        )
    }

    @Test
    fun `board object tokens are NOT overridden - the board stays dark warm wood`() {
        val overridden = colorMap(brightColorsPath).keys.intersect(boardObjectTokens)
        assertTrue(
            "The chess board is its own designed object and keeps the base palette in " +
                "both modes (ChessBoardView paints matching literals — flipping only the " +
                "resource side would desync them): $overridden",
            overridden.isEmpty(),
        )
    }

    @Test
    fun `no bright override copies its dark value - each must actually flip`() {
        val base = colorMap(baseColorsPath)
        val copied = colorMap(brightColorsPath).filter { (name, value) ->
            base[name]?.equals(value, ignoreCase = true) == true
        }.keys
        assertTrue(
            "These values-notnight overrides are byte-identical to the dark values — " +
                "either drop the override or supply the bright variant: $copied",
            copied.isEmpty(),
        )
    }

    @Test
    fun `bright overrides are literal hexes - no alias indirection`() {
        val nonHex = colorMap(brightColorsPath).filterValues {
            !Regex("""#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?""").matches(it)
        }
        assertTrue(
            "values-notnight colors must be literal #RGB hexes (aliases resolve in the " +
                "base file and would double-indirect here): $nonHex",
            nonHex.isEmpty(),
        )
    }

    // ── 2b · theme variant parity ────────────────────────────────────

    private fun atriumTheme(path: String): Element {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(File(path))
        val styles = doc.getElementsByTagName("style")
        for (i in 0 until styles.length) {
            val el = styles.item(i) as Element
            if (el.getAttribute("name") == "Theme.Cereveon.Atrium") return el
        }
        error("No Theme.Cereveon.Atrium style in $path")
    }

    private fun itemMap(style: Element): Map<String, String> {
        val items = style.getElementsByTagName("item")
        return (0 until items.length).associate { i ->
            val el = items.item(i) as Element
            el.getAttribute("name") to el.textContent.trim()
        }
    }

    @Test
    fun `theme variants keep their parents - Dark by default, Light for bright`() {
        assertEquals(
            "Base theme must stay parented on the non-DayNight Dark theme (dark is " +
                "the default posture; the system toggle must not flip it).",
            "Theme.Material3.Dark.NoActionBar",
            atriumTheme(baseThemesPath).getAttribute("parent"),
        )
        assertEquals(
            "Bright variant must re-parent on the Light theme so Material widget " +
                "internals resolve light defaults.",
            "Theme.Material3.Light.NoActionBar",
            atriumTheme(brightThemesPath).getAttribute("parent"),
        )
    }

    @Test
    fun `theme variants declare the same attribute set - the two blocks must not drift`() {
        val base = itemMap(atriumTheme(baseThemesPath)).keys
        val bright = itemMap(atriumTheme(brightThemesPath)).keys
        assertEquals(
            "values/themes.xml and values-notnight/themes.xml declare different " +
                "attributes for Theme.Cereveon.Atrium — an attribute added to one " +
                "block only would silently fall back to the Material default in the " +
                "other mode.  Base-only: ${base - bright}; bright-only: ${bright - base}.",
            base,
            bright,
        )
    }

    @Test
    fun `system bar icons flip with the palette`() {
        val base = itemMap(atriumTheme(baseThemesPath))
        val bright = itemMap(atriumTheme(brightThemesPath))
        assertEquals("false", base["android:windowLightStatusBar"])
        assertEquals("true", bright["android:windowLightStatusBar"])
        assertEquals("true", bright["android:windowLightNavigationBar"])
    }

    // ── 3 · wiring source pins ───────────────────────────────────────

    private fun countIdDeclarations(xml: String, viewId: String): Int =
        Regex("""android:id\s*=\s*"@\+id/$viewId"""").findAll(xml).count()

    @Test
    fun `settings layout declares the bright mode row and switch exactly once`() {
        val xml = File(settingsLayoutPath).readText()
        assertEquals(1, countIdDeclarations(xml, "rowBrightMode"))
        assertEquals(1, countIdDeclarations(xml, "switchBrightMode"))
    }

    @Test
    fun `application applies the persisted appearance before the keystore prewarm`() {
        val kt = File(applicationPath).readText()
        assertTrue(
            "CereveonApplication must map the pref through SettingsBottomSheet.nightModeFor " +
                "(the single mapping the tests above pin).",
            kt.contains("SettingsBottomSheet.nightModeFor") &&
                kt.contains("SettingsBottomSheet.readBrightModeEnabled"),
        )
        val apply = kt.indexOf("applyPersistedAppearance()")
        val prewarm = kt.indexOf("prewarmEncryptedTokenStorage()")
        assertTrue(
            "onCreate must apply the appearance (synchronous, before any activity " +
                "inflates) and may then fire the async keystore prewarm.",
            apply in 0 until prewarm,
        )
    }

    @Test
    fun `settings switch persists then dismisses BEFORE flipping the night mode`() {
        val kt = File(settingsSheetPath).readText()
        val listenerStart = kt.indexOf("bright.setOnCheckedChangeListener")
        assertTrue(
            "SettingsBottomSheet must wire switchBrightMode's checked-change listener.",
            listenerStart >= 0,
        )
        val block = kt.substring(listenerStart, kt.indexOf("switchSound", listenerStart))
        val persist = block.indexOf("putBoolean(PREF_BRIGHT_MODE")
        val dismiss = block.indexOf("dismiss()")
        val applyMode = block.indexOf("AppCompatDelegate.setDefaultNightMode")
        assertTrue(
            "The listener must persist the pref, then dismiss(), then flip the mode — " +
                "in that order.  Flipping first recreates the host while the sheet is " +
                "showing; the framework-restored sheet has null Account callbacks " +
                "(they are wired at show-time).  Found offsets: persist=$persist, " +
                "dismiss=$dismiss, setDefaultNightMode=$applyMode.",
            persist in 0 until dismiss && dismiss < applyMode,
        )
    }
}
