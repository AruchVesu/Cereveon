package ai.chesscoach.app

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
        assertNotNull(v.findViewById<View>(R.id.homeDateKicker))
        assertNotNull(v.findViewById<View>(R.id.homeSyncIndicator))
        assertNotNull(v.findViewById<View>(R.id.homeSyncIndicatorText))
        assertNotNull(v.findViewById<View>(R.id.homeResumeBlock))
        assertNotNull(v.findViewById<View>(R.id.homeRowNewGame))
        assertNotNull(v.findViewById<View>(R.id.homeRowLessons))
        assertNotNull(v.findViewById<View>(R.id.homeRowOpenings))
        assertNotNull(v.findViewById<View>(R.id.homeRowPastGames))
        assertNotNull(v.findViewById<View>(R.id.homeTabHome))
        assertNotNull(v.findViewById<View>(R.id.homeTabLessons))
        assertNotNull(v.findViewById<View>(R.id.homeTabCoach))
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
    fun activity_openings_inflates() {
        val v = inflate(R.layout.activity_openings)
        assertNotNull(v.findViewById<View>(R.id.openingsHeader))
        assertNotNull(v.findViewById<View>(R.id.openingsStats))
        assertNotNull(v.findViewById<View>(R.id.openingsCardContainer))
        assertNotNull(v.findViewById<View>(R.id.btnOpeningsDrill))
        assertNotNull(v.findViewById<View>(R.id.btnOpeningsAdd))
    }

    @Test
    fun item_opening_card_inflates() {
        val v = inflate(R.layout.item_opening_card)
        assertNotNull(v.findViewById<View>(R.id.openingEco))
        assertNotNull(v.findViewById<View>(R.id.openingName))
        assertNotNull(v.findViewById<View>(R.id.openingLine))
        assertNotNull(v.findViewById<View>(R.id.openingMasteryFill))
        assertNotNull(v.findViewById<View>(R.id.openingMasteryPct))
        assertNotNull(v.findViewById<View>(R.id.openingActiveBadge))
    }

    @Test
    fun dialog_add_opening_inflates() {
        val v = inflate(R.layout.dialog_add_opening)
        assertNotNull(v.findViewById<View>(R.id.dialogAddOpeningEco))
        assertNotNull(v.findViewById<View>(R.id.dialogAddOpeningName))
        assertNotNull(v.findViewById<View>(R.id.dialogAddOpeningLine))
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
        inflate(R.layout.bottom_sheet_progress_dashboard)
    }

    @Test
    fun bottom_sheet_training_session_inflates() {
        inflate(R.layout.bottom_sheet_training_session)
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
}
