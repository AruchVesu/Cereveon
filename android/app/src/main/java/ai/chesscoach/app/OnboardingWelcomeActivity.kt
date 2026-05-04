package ai.chesscoach.app

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * Cereveon · Atrium · Onboarding · Welcome (handoff step 1 of 3).
 *
 * Pure informational screen — sets up the app's value proposition
 * before [OnboardingActivity] (step 2/3) asks the user to calibrate
 * their skill.  Tapping Begin advances to the calibration screen;
 * there's no Back path (the user is already authenticated and
 * [LoginActivity.launchPostAuth] would just bounce them back here
 * until [OnboardingActivity.PREF_ONBOARDING_COMPLETED] is true).
 *
 * Bullets are inflated dynamically from [DEFAULT_HOOKS] using the
 * same item_paywall_bullet primitive the paywall ✦-bullet list uses
 * — keeps the typographic rhyme between marketing-style screens
 * consistent without duplicating the row layout.
 */
class OnboardingWelcomeActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding_welcome)

        renderBullets(findViewById(R.id.onboardingWelcomeBullets))

        findViewById<Button>(R.id.btnOnboardingWelcomeBegin).setOnClickListener {
            startActivity(
                Intent(this, OnboardingActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
            finish()
        }
    }

    private fun renderBullets(container: LinearLayout) {
        container.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (text in DEFAULT_HOOKS) {
            val row = inflater.inflate(R.layout.item_paywall_bullet, container, false)
            row.findViewById<TextView>(R.id.paywallBulletText).text = text
            container.addView(row)
        }
    }

    companion object {
        /**
         * What the user gets — three short Cormorant-italic lines.
         * Order matters: most concrete benefit first, the
         * "personalised" framing last so the eye lands on it before
         * tapping Begin.
         */
        val DEFAULT_HOOKS: List<String> = listOf(
            "Adaptive opponents at your level",
            "Coach chat grounded in your games",
            "A study that grows with you",
        )
    }
}
