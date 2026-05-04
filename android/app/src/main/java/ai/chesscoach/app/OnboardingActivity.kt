package ai.chesscoach.app

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.slider.Slider
import kotlin.math.roundToInt
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Onboarding · Skill calibration (handoff #2).
 *
 * Shown ONCE after registration so the adaptation layer can dispatch
 * a first opponent at the right level.  LoginActivity routes any
 * authenticated user here until [PREF_ONBOARDING_COMPLETED] is true,
 * so this screen has no "real" Back path — see the Back button
 * comment in [onCreate] for the skip-with-defaults semantics.
 *
 * Persistence: SharedPreferences "chesscoach_prefs" (the same store
 * MainActivity / SettingsBottomSheet use):
 *   onboarding_completed: Boolean — true once Continue is tapped
 *   player_rating_estimate: Float  — slider value, e.g. 1720f
 *   player_confidence: Float       — sure=0.85, guessing=0.5, rusty=0.25
 *
 * Server sync: [persistAndContinue] also fires a best-effort PATCH
 * /auth/me with the new values so the backend's adaptation layer sees
 * the calibration before the first game.  If the call fails (offline,
 * server down) the values stay in SharedPreferences and MainActivity's
 * cold-start sync reconciles by re-PATCHing — see
 * MainActivity.PREF_PLAYER_RATING_ESTIMATE / RATING_RECONCILE_EPSILON.
 */
class OnboardingActivity : AppCompatActivity() {

    private lateinit var slider: Slider
    private lateinit var ratingValue: TextView
    private lateinit var firstOpponent: TextView

    private val confidenceDots = mutableMapOf<String, View>()
    private var selectedConfidence: String = DEFAULT_CONFIDENCE

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding)

        slider        = findViewById(R.id.sliderRating)
        ratingValue   = findViewById(R.id.txtRatingValue)
        firstOpponent = findViewById(R.id.txtFirstOpponent)

        // ── Slider — live-update the hero value + first-opponent preview.
        slider.value = DEFAULT_RATING
        ratingValue.text = formatRating(DEFAULT_RATING)
        firstOpponent.text = formatFirstOpponent(DEFAULT_RATING)
        slider.addOnChangeListener { _, value, _ ->
            ratingValue.text = formatRating(value)
            firstOpponent.text = formatFirstOpponent(value)
        }

        // ── Confidence radio.
        confidenceDots["sure"]     = findViewById(R.id.confSureDot)
        confidenceDots["guessing"] = findViewById(R.id.confGuessingDot)
        confidenceDots["rusty"]    = findViewById(R.id.confRustyDot)
        applyConfidenceState(DEFAULT_CONFIDENCE)

        bindConfidenceRow(R.id.confSure)
        bindConfidenceRow(R.id.confGuessing)
        bindConfidenceRow(R.id.confRusty)

        // ── Footer.
        // "Back" here means "skip calibration": the user is already
        // authenticated (registration succeeded) and LoginActivity will
        // bounce them right back here until [PREF_ONBOARDING_COMPLETED]
        // is true.  So pressing Back persists DEFAULT_RATING + the
        // current confidence and continues to MainActivity, where the
        // rating can later be adjusted from the drawer.
        findViewById<Button>(R.id.btnOnboardingBack).setOnClickListener {
            slider.value = DEFAULT_RATING
            persistAndContinue()
        }

        findViewById<Button>(R.id.btnOnboardingContinue).setOnClickListener {
            persistAndContinue()
        }
    }

    private fun bindConfidenceRow(rowId: Int) {
        val row = findViewById<LinearLayout>(rowId)
        val value = row.tag as String
        row.setOnClickListener {
            selectedConfidence = value
            applyConfidenceState(value)
        }
    }

    private fun applyConfidenceState(selected: String) {
        val filled = ContextCompat.getDrawable(this, R.drawable.atrium_radio_selected)
        val hollow = ContextCompat.getDrawable(this, R.drawable.atrium_radio_unselected)
        confidenceDots.forEach { (key, dot) ->
            dot.background = if (key == selected) filled else hollow
        }
        selectedConfidence = selected
    }

    private fun persistAndContinue() {
        val rating = slider.value
        val confidence = confidenceFromKey(selectedConfidence)
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
            .putBoolean(PREF_ONBOARDING_COMPLETED, true)
            .putFloat(PREF_PLAYER_RATING_ESTIMATE, rating)
            .putFloat(PREF_PLAYER_CONFIDENCE, confidence)
            // Mirror the rating into the existing PREF_RATING key so
            // MainActivity's drawer header (and the Home screen's "I —
            // New game" sub) pick it up immediately without waiting
            // for the next /auth/me sync.
            .putFloat(MainActivity.PREF_RATING, rating)
            .apply()

        // Best-effort PATCH /auth/me so the server's adaptation layer
        // sees the calibration before the first game.  If the network
        // call fails (offline, server down) the values stay in
        // SharedPreferences and MainActivity's cold-start sync
        // reconciles by comparing the cached PREF_PLAYER_RATING_ESTIMATE
        // against the server-returned rating and re-PATCHing.
        firePatchAuthMe(rating, confidence)

        // Step 3/3 reads back what we just saved so the user sees the
        // calibration confirmed before the first game; that screen
        // owns the final "go to Home" navigation.
        startActivity(Intent(this, OnboardingCompleteActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }

    private fun firePatchAuthMe(rating: Float, confidence: Float) {
        val authRepo = AuthRepository(EncryptedTokenStorage(this))
        val token = authRepo.getToken() ?: return
        val client: AuthApiClient = HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            // Same X-Auth-Token rotation as MainActivity — see kdoc there.
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        // Fire on the activity's lifecycleScope rather than
        // GlobalScope: if the user backgrounds the app immediately
        // after Continue, the launch is cancelled and we don't leak
        // a coroutine.  Recovery path in MainActivity will retry on
        // the next cold-start.
        lifecycleScope.launch {
            when (val r = client.updateMe(token, rating = rating, confidence = confidence)) {
                is ApiResult.Success -> Log.d(
                    "ONBOARDING",
                    "PATCH /auth/me OK (rating=${r.data.rating}, confidence=${r.data.confidence})",
                )
                is ApiResult.HttpError -> Log.w("ONBOARDING", "PATCH /auth/me HTTP ${r.code}")
                is ApiResult.NetworkError -> Log.w("ONBOARDING", "PATCH /auth/me network error", r.cause)
                ApiResult.Timeout -> Log.w("ONBOARDING", "PATCH /auth/me timed out")
            }
        }
    }

    companion object {
        const val PREFS_NAME = MainActivity.PREFS_NAME

        const val PREF_ONBOARDING_COMPLETED    = "onboarding_completed"
        const val PREF_PLAYER_RATING_ESTIMATE  = "player_rating_estimate"
        const val PREF_PLAYER_CONFIDENCE       = "player_confidence"

        const val DEFAULT_RATING: Float = 1500f
        const val DEFAULT_CONFIDENCE: String = "guessing"

        /** Whether this account has been through the calibration flow. */
        fun isCompleted(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_ONBOARDING_COMPLETED, false)

        /** Format a slider value as a bare integer rating string. */
        fun formatRating(value: Float): String = value.roundToInt().toString()

        /**
         * Compute the first-opponent preview row text for [rating].
         * Per the handoff: opponent rating is biased ~40 below the
         * player's estimate so the first match is gentle but
         * adaptive will close the gap quickly.
         */
        fun formatFirstOpponent(rating: Float): String {
            val opponent = (rating - 40f).coerceAtLeast(800f).roundToInt()
            return "~$opponent · adaptive"
        }

        /**
         * Map a confidence radio key to a 0.0–1.0 value used by the
         * adaptation layer.  The values match the handoff intent —
         * "Sure of it" implies near-true rating, "Rusty" implies
         * a wide uncertainty band so adaptive moves faster.
         */
        fun confidenceFromKey(key: String): Float = when (key.lowercase()) {
            "sure"     -> 0.85f
            "guessing" -> 0.50f
            "rusty"    -> 0.25f
            else       -> 0.50f
        }
    }
}
