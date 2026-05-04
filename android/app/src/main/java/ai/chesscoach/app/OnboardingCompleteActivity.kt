package ai.chesscoach.app

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * Cereveon · Atrium · Onboarding · Completion (handoff step 3 of 3).
 *
 * Reads back the calibration the user just supplied so they see what
 * was recorded before the first game.  Reached from
 * [OnboardingActivity.persistAndContinue]; tapping
 * "Play your first game" routes to [HomeActivity], which is the same
 * post-auth landing every other path lands on.
 *
 * No Back path — by the time the user hits this screen they've
 * already saved the calibration to prefs; the only forward action
 * is "go play".
 */
class OnboardingCompleteActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding_complete)

        val prefs = getSharedPreferences(OnboardingActivity.PREFS_NAME, Context.MODE_PRIVATE)
        val rating = prefs.getFloat(
            OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
            OnboardingActivity.DEFAULT_RATING,
        )
        val confidence = prefs.getFloat(
            OnboardingActivity.PREF_PLAYER_CONFIDENCE,
            OnboardingActivity.confidenceFromKey(OnboardingActivity.DEFAULT_CONFIDENCE),
        )

        findViewById<TextView>(R.id.onboardingCompleteRating).text =
            OnboardingActivity.formatRating(rating)
        findViewById<TextView>(R.id.onboardingCompleteConfidence).text =
            formatConfidenceLabel(confidence)
        findViewById<TextView>(R.id.onboardingCompleteOpponent).text =
            OnboardingActivity.formatFirstOpponent(rating)

        findViewById<Button>(R.id.btnOnboardingCompleteStart).setOnClickListener {
            startActivity(
                Intent(this, HomeActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
        }
    }

    companion object {
        /**
         * Map a 0–1 confidence weight back to a human-readable label
         * matching [OnboardingActivity]'s 3-row radio.  Inverse of
         * [OnboardingActivity.confidenceFromKey] up to a small
         * tolerance so float round-trip noise from the
         * SharedPreferences write/read doesn't bucket a clear "Sure
         * of it" choice into "Guessing".
         *
         * The buckets:
         *   ≥ 0.70 → "Sure of it"   (sure-key value 0.85)
         *   ≥ 0.40 → "Guessing"     (guessing-key value 0.50)
         *    <0.40 → "Rusty"        (rusty-key value 0.25)
         */
        fun formatConfidenceLabel(confidence: Float): String = when {
            confidence >= 0.70f -> "Sure of it"
            confidence >= 0.40f -> "Guessing"
            else                -> "Rusty"
        }
    }
}
