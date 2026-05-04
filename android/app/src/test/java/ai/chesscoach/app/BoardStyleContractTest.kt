package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pin the contract between [SettingsBottomSheet] (which persists the
 * user-chosen board variant) and [ChessBoardView] (which renders it).
 * The setting is a plain string round-tripped through SharedPreferences,
 * so a typo on either side would silently fall back to [STYLE_FLAT]
 * and the user's pick would have no visible effect.  This test fails
 * loud at build time instead.
 */
class BoardStyleContractTest {

    @Test
    fun `default board style matches between settings and view`() {
        assertEquals(
            "SettingsBottomSheet.DEFAULT_BOARD_STYLE drifted from ChessBoardView.DEFAULT_BOARD_STYLE",
            ChessBoardView.DEFAULT_BOARD_STYLE,
            SettingsBottomSheet.DEFAULT_BOARD_STYLE,
        )
    }

    @Test
    fun `view supports every variant the settings sheet can persist`() {
        // Row tags in res/layout/bottom_sheet_settings.xml — kept in sync
        // with the radio rows the user can tap.  If a new variant lands,
        // add it here and to ChessBoardView.SUPPORTED_BOARD_STYLES.
        val settingsVariants = setOf("flat", "engraved", "wireframe")
        assertEquals(
            "Settings layout row tags drifted from this contract test",
            settingsVariants,
            ChessBoardView.SUPPORTED_BOARD_STYLES,
        )
    }

    @Test
    fun `default style is one of the supported variants`() {
        assertTrue(
            ChessBoardView.DEFAULT_BOARD_STYLE in ChessBoardView.SUPPORTED_BOARD_STYLES,
        )
    }
}
