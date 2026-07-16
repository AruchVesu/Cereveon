package ai.chesscoach.app

import android.content.res.Configuration
import android.view.LayoutInflater
import android.view.View
import androidx.appcompat.view.ContextThemeWrapper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Smoke tests that inflate every Atrium-themed layout against a real
 * Android resource pipeline.  Catches the class of bugs the host-JVM
 * unit tests cannot see:
 *
 *   - AAPT2 link errors that survive `assembleDebug` because they only
 *     surface at inflation time (e.g. a `?attr/` reference that the
 *     theme doesn't actually expose, or a drawable that resolves at
 *     compile time but throws Resources$NotFoundException when read)
 *   - Style inheritance chains that break under
 *     `ContextThemeWrapper(Theme.Cereveon.Atrium)` because of a missing
 *     `parent=""` opt-out on a dot-named style (this bug bit us twice
 *     during the Atrium rollout — see the `Atrium.Divider` /
 *     `Atrium.SettingsRow` kdoc comments)
 *   - Layout XML that references a removed/renamed view ID, font, or
 *     drawable
 *
 * We inflate without launching activities so the tests don't need
 * authenticated state, network reachability, or the JNI engine — they
 * just verify the resource graph is internally consistent.  Activity
 * lifecycle (onCreate findViewById, listener wiring) is covered by
 * the host-JVM tests against the same XML IDs.
 *
 * NOTE: Requires a connected device or emulator.  Run via
 * `./gradlew :app:connectedAndroidTest`.
 */
@RunWith(AndroidJUnit4::class)
class AtriumLayoutInflationTest {

    private val themedContext: ContextThemeWrapper by lazy {
        ContextThemeWrapper(
            InstrumentationRegistry.getInstrumentation().targetContext,
            R.style.Theme_Cereveon_Atrium,
        )
    }

    private fun inflate(layoutId: Int): View =
        LayoutInflater.from(themedContext).inflate(layoutId, null)

    // ── Activities ───────────────────────────────────────────────────

    @Test
    fun activity_login_inflates() {
        val v = inflate(R.layout.activity_login)
        // Spot-check the IDs LoginActivity.onCreate findViewByIds; if
        // an upstream XML rename slipped through, the test surfaces
        // the missing ID immediately rather than waiting for the
        // first user that taps Sign In.
        assertNotNull(v.findViewById<View>(R.id.btnLogin))
        assertNotNull(v.findViewById<View>(R.id.btnRegister))
        assertNotNull(v.findViewById<View>(R.id.etEmail))
        assertNotNull(v.findViewById<View>(R.id.etPassword))
    }

    @Test
    fun activity_onboarding_inflates() {
        val v = inflate(R.layout.activity_onboarding)
        assertNotNull(v.findViewById<View>(R.id.sliderRating))
        assertNotNull(v.findViewById<View>(R.id.txtRatingValue))
        assertNotNull(v.findViewById<View>(R.id.txtFirstOpponent))
        assertNotNull(v.findViewById<View>(R.id.btnOnboardingBack))
        assertNotNull(v.findViewById<View>(R.id.btnOnboardingContinue))
        assertNotNull(v.findViewById<View>(R.id.confSure))
        assertNotNull(v.findViewById<View>(R.id.confGuessing))
        assertNotNull(v.findViewById<View>(R.id.confRusty))
    }

    @Test
    fun activity_home_inflates() {
        val v = inflate(R.layout.activity_home)
        assertNotNull(v.findViewById<View>(R.id.homeAvatar))
        assertNotNull(v.findViewById<View>(R.id.homeAvatarTapTarget))
        assertNotNull(v.findViewById<View>(R.id.homeDateKicker))
        assertNotNull(v.findViewById<View>(R.id.homeSyncIndicator))
        assertNotNull(v.findViewById<View>(R.id.homeSyncIndicatorText))
        assertNotNull(v.findViewById<View>(R.id.homeResumeBlock))
        assertNotNull(v.findViewById<View>(R.id.homeRowNewGame))
        assertNotNull(v.findViewById<View>(R.id.homeRowPastGames))
        assertNotNull(v.findViewById<View>(R.id.homeTabHome))
        assertNotNull(v.findViewById<View>(R.id.homeTabPuzzles))
        assertNotNull(v.findViewById<View>(R.id.homeTabYou))
    }

    @Test
    fun activity_main_inflates() {
        // The biggest layout we ship — drawer + chess board + Atrium
        // chapter header + eval band + coach paragraph + footer.  A
        // theme attribute mismatch usually shows up here first.
        inflate(R.layout.activity_main)
    }

    @Test
    fun activity_paywall_inflates() {
        val v = inflate(R.layout.activity_paywall)
        assertNotNull(v.findViewById<View>(R.id.paywallFeatures))
        assertNotNull(v.findViewById<View>(R.id.paywallPlanMonthly))
        assertNotNull(v.findViewById<View>(R.id.paywallPlanYearly))
        assertNotNull(v.findViewById<View>(R.id.paywallPlanMonthlyPrice))
        assertNotNull(v.findViewById<View>(R.id.paywallPlanYearlyPrice))
        assertNotNull(v.findViewById<View>(R.id.btnPaywallBegin))
        assertNotNull(v.findViewById<View>(R.id.btnPaywallMaybeLater))
    }

    @Test
    fun item_paywall_bullet_inflates() {
        val v = inflate(R.layout.item_paywall_bullet)
        assertNotNull(v.findViewById<View>(R.id.paywallBulletText))
    }

    // ── Bottom sheets ────────────────────────────────────────────────

    @Test
    fun bottom_sheet_settings_inflates() {
        val v = inflate(R.layout.bottom_sheet_settings)
        assertNotNull(v.findViewById<View>(R.id.voiceFormalDot))
        assertNotNull(v.findViewById<View>(R.id.boardFlatDot))
        assertNotNull(v.findViewById<View>(R.id.rowBrightMode))
        assertNotNull(v.findViewById<View>(R.id.switchBrightMode))
        assertNotNull(v.findViewById<View>(R.id.switchSound))
        assertNotNull(v.findViewById<View>(R.id.switchNotifications))
        assertNotNull(v.findViewById<View>(R.id.rowEditRating))
        assertNotNull(v.findViewById<View>(R.id.rowEditRatingValue))
        assertNotNull(v.findViewById<View>(R.id.rowUpgrade))
        assertNotNull(v.findViewById<View>(R.id.rowChangePassword))
        assertNotNull(v.findViewById<View>(R.id.rowSignOut))
    }

    @Test
    fun dialog_edit_rating_inflates() {
        val v = inflate(R.layout.dialog_edit_rating)
        assertNotNull(v.findViewById<View>(R.id.dialogRatingValue))
        assertNotNull(v.findViewById<View>(R.id.dialogRatingSlider))
    }

    @Test
    fun bottom_sheet_game_summary_inflates() {
        inflate(R.layout.bottom_sheet_game_summary)
    }

    @Test
    fun bottom_sheet_progress_dashboard_inflates() {
        val v = inflate(R.layout.bottom_sheet_progress_dashboard)
        // Human-progress header IDs resolved by
        // ProgressDashboardBottomSheet.onViewCreated.
        assertNotNull(v.findViewById<View>(R.id.heroLevelBlock))
        assertNotNull(v.findViewById<View>(R.id.txtRating))
        assertNotNull(v.findViewById<View>(R.id.txtHeroXp))
        assertNotNull(v.findViewById<View>(R.id.statGamesRow))
        assertNotNull(v.findViewById<View>(R.id.statGamesDivider))
        assertNotNull(v.findViewById<View>(R.id.txtConfidence))
    }

    @Test
    fun bottom_sheet_study_plan_overview_inflates() {
        val v = inflate(R.layout.bottom_sheet_study_plan_overview)
        // Spot-check the IDs StudyPlanOverviewBottomSheet.onViewCreated
        // resolves; an XML rename would surface here rather than at the
        // first user who opens "This week's plan".
        assertNotNull(v.findViewById<View>(R.id.overviewFocus))
        assertNotNull(v.findViewById<View>(R.id.overviewVerdict))
        assertNotNull(v.findViewById<View>(R.id.overviewProgress))
        assertNotNull(v.findViewById<View>(R.id.overviewDay1Label))
        assertNotNull(v.findViewById<View>(R.id.overviewDay1Status))
        assertNotNull(v.findViewById<View>(R.id.overviewDay2Label))
        assertNotNull(v.findViewById<View>(R.id.overviewDay3Label))
        assertNotNull(v.findViewById<View>(R.id.overviewStartButton))
        assertNotNull(v.findViewById<View>(R.id.overviewPracticeButton))
        assertNotNull(v.findViewById<View>(R.id.overviewCloseButton))
    }

    @Test
    fun bottom_sheet_puzzle_trainer_inflates() {
        val v = inflate(R.layout.bottom_sheet_puzzle_trainer)
        // Spot-check the IDs PuzzleTrainerBottomSheet.onViewCreated
        // resolves; an XML rename would surface here rather than at the
        // first user who opens the Puzzles tab.
        assertNotNull(v.findViewById<View>(R.id.puzzleTrainerKicker))
        assertNotNull(v.findViewById<View>(R.id.puzzleTrainerTitle))
        assertNotNull(v.findViewById<View>(R.id.puzzleTrainerBoard))
        assertNotNull(v.findViewById<View>(R.id.puzzleTrainerStatus))
        assertNotNull(v.findViewById<View>(R.id.puzzleTrainerNextButton))
        assertNotNull(v.findViewById<View>(R.id.puzzleTrainerCloseButton))
    }

    @Test
    fun bottom_sheet_game_history_inflates() {
        inflate(R.layout.bottom_sheet_game_history)
    }

    @Test
    fun sheet_chat_inflates() {
        inflate(R.layout.sheet_chat)
    }

    // ── Dialogs + recycled item layouts ──────────────────────────────

    @Test
    fun dialog_promotion_inflates() {
        inflate(R.layout.dialog_promotion)
    }

    @Test
    fun item_chat_coach_inflates() {
        inflate(R.layout.item_chat_coach)
    }

    @Test
    fun item_chat_user_inflates() {
        inflate(R.layout.item_chat_user)
    }

    @Test
    fun activity_onboarding_welcome_inflates() {
        val v = inflate(R.layout.activity_onboarding_welcome)
        assertNotNull(v.findViewById<View>(R.id.onboardingWelcomeBullets))
        assertNotNull(v.findViewById<View>(R.id.btnOnboardingWelcomeBegin))
    }

    @Test
    fun activity_onboarding_complete_inflates() {
        val v = inflate(R.layout.activity_onboarding_complete)
        assertNotNull(v.findViewById<View>(R.id.onboardingCompleteRating))
        assertNotNull(v.findViewById<View>(R.id.onboardingCompleteConfidence))
        assertNotNull(v.findViewById<View>(R.id.onboardingCompleteOpponent))
        assertNotNull(v.findViewById<View>(R.id.btnOnboardingCompleteStart))
    }

    // ── Bright mode (values-notnight) ────────────────────────────────
    //
    // Bright mode re-parents Theme.Cereveon.Atrium on Material3.Light
    // and swaps the token palette via values-notnight/colors.xml
    // (Settings › Appearance › Bright mode → MODE_NIGHT_NO).  Inflate
    // the heaviest screens under BOTH uiMode configurations so a
    // resource that resolves in one qualifier but not the other
    // (missing notnight parent attr, dangling @color) fails here
    // rather than on the first user toggle.  The device's own uiMode
    // makes the suite above cover one config; these pin both
    // explicitly.

    private fun themedContext(nightModeFlag: Int): ContextThemeWrapper {
        val base = InstrumentationRegistry.getInstrumentation().targetContext
        val config = Configuration(base.resources.configuration)
        config.uiMode =
            nightModeFlag or (config.uiMode and Configuration.UI_MODE_NIGHT_MASK.inv())
        return ContextThemeWrapper(
            base.createConfigurationContext(config),
            R.style.Theme_Cereveon_Atrium,
        )
    }

    private fun inflateIn(nightModeFlag: Int, layoutId: Int): View =
        LayoutInflater.from(themedContext(nightModeFlag)).inflate(layoutId, null)

    @Test
    fun bottom_sheet_settings_inflates_in_bright_mode() {
        val v = inflateIn(Configuration.UI_MODE_NIGHT_NO, R.layout.bottom_sheet_settings)
        assertNotNull(v.findViewById<View>(R.id.rowBrightMode))
        assertNotNull(v.findViewById<View>(R.id.switchBrightMode))
    }

    @Test
    fun bottom_sheet_settings_inflates_in_dark_mode() {
        val v = inflateIn(Configuration.UI_MODE_NIGHT_YES, R.layout.bottom_sheet_settings)
        assertNotNull(v.findViewById<View>(R.id.rowBrightMode))
        assertNotNull(v.findViewById<View>(R.id.switchBrightMode))
    }

    @Test
    fun activity_home_inflates_in_bright_mode() {
        assertNotNull(inflateIn(Configuration.UI_MODE_NIGHT_NO, R.layout.activity_home))
    }

    @Test
    fun activity_main_inflates_in_bright_mode() {
        assertNotNull(inflateIn(Configuration.UI_MODE_NIGHT_NO, R.layout.activity_main))
    }

    @Test
    fun activity_main_inflates_in_dark_mode() {
        assertNotNull(inflateIn(Configuration.UI_MODE_NIGHT_YES, R.layout.activity_main))
    }
}
