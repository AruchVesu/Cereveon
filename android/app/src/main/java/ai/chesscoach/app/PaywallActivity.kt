package ai.chesscoach.app

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

/**
 * Cereveon · Atrium · Paywall (handoff screen #11 — final Atrium screen).
 *
 * Reached from SettingsBottomSheet → "Upgrade · Premium" chevron row.
 * Static surface for the scaffold pass — no Stripe / Google Play
 * Billing wiring exists yet.  When billing lands the wiring becomes:
 *   - Plans driven by the platform's product catalog (Play Billing
 *     SkuDetails / Stripe Prices) rather than [DEFAULT_PLANS]
 *   - Begin button starts the platform's purchase flow with the
 *     selected plan's SKU
 *   - "Maybe later" stays the same (just dismisses the sheet)
 *
 * For now Begin toasts "coming soon · {plan}" so the click event is
 * at least observable; "Maybe later" finishes the activity.
 *
 * Plan selection state
 * --------------------
 * Lives only in memory ([selectedPlanKey]) for the scaffold.  The
 * design defaults to "yearly" (the recommended plan), and the user
 * can toggle to "monthly" by tapping the other tile — the tap swaps
 * the background drawable + price colour to mirror the design's
 * active/dormant treatment.
 */
class PaywallActivity : AppCompatActivity() {

    private var selectedPlanKey: String = "yearly"
    private lateinit var monthlyTile: FrameLayout
    private lateinit var yearlyTile: FrameLayout
    private lateinit var monthlyPrice: TextView
    private lateinit var yearlyPrice: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_paywall)

        renderFeatureBullets(findViewById(R.id.paywallFeatures))

        monthlyTile  = findViewById(R.id.paywallPlanMonthly)
        yearlyTile   = findViewById(R.id.paywallPlanYearly)
        monthlyPrice = findViewById(R.id.paywallPlanMonthlyPrice)
        yearlyPrice  = findViewById(R.id.paywallPlanYearlyPrice)

        monthlyTile.setOnClickListener { selectPlan("monthly") }
        yearlyTile.setOnClickListener { selectPlan("yearly") }
        // Initial state matches the design — yearly active by default.
        selectPlan(selectedPlanKey)

        findViewById<Button>(R.id.btnPaywallBegin).setOnClickListener {
            val plan = DEFAULT_PLANS.firstOrNull { it.key == selectedPlanKey }
            val label = plan?.title ?: selectedPlanKey
            Toast.makeText(this, "Begin $label · coming soon", Toast.LENGTH_SHORT).show()
        }
        findViewById<TextView>(R.id.btnPaywallMaybeLater).setOnClickListener {
            finish()
        }
    }

    private fun renderFeatureBullets(container: LinearLayout) {
        container.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (text in DEFAULT_FEATURES) {
            val row = inflater.inflate(R.layout.item_paywall_bullet, container, false)
            row.findViewById<TextView>(R.id.paywallBulletText).text = text
            container.addView(row)
        }
    }

    private fun selectPlan(key: String) {
        selectedPlanKey = key
        val isMonthly = key == "monthly"

        monthlyTile.background = ContextCompat.getDrawable(
            this,
            if (isMonthly) R.drawable.atrium_paywall_plan_active
            else R.drawable.atrium_paywall_plan_dormant,
        )
        yearlyTile.background = ContextCompat.getDrawable(
            this,
            if (isMonthly) R.drawable.atrium_paywall_plan_dormant
            else R.drawable.atrium_paywall_plan_active,
        )
        monthlyPrice.setTextColor(
            ContextCompat.getColor(
                this,
                if (isMonthly) R.color.atrium_accent_cyan else R.color.atrium_ink,
            ),
        )
        yearlyPrice.setTextColor(
            ContextCompat.getColor(
                this,
                if (isMonthly) R.color.atrium_ink else R.color.atrium_accent_cyan,
            ),
        )
    }

    /** One subscription plan tile in the paywall's 2-column grid. */
    data class Plan(
        val key: String,
        val title: String,
        val price: String,
        val sub: String,
        val isRecommended: Boolean,
    )

    companion object {
        /**
         * Hardcoded default plans matching the design 1-for-1.  Lifted
         * to the companion so unit tests can verify the canonical
         * shape without launching the activity.  The "yearly" entry
         * is marked recommended (drives the initial active-tile
         * selection); when Play Billing lands, the recommendation
         * lives in the SkuDetails rollout config rather than here.
         */
        val DEFAULT_PLANS: List<Plan> = listOf(
            Plan(
                key = "monthly",
                title = "Monthly",
                price = "$9",
                sub = "per month",
                isRecommended = false,
            ),
            Plan(
                key = "yearly",
                title = "Yearly",
                price = "$72",
                sub = "$6 / month",
                isRecommended = true,
            ),
        )

        /**
         * Bullet copy for the feature list.  4 items per the design;
         * order matters (the most concrete benefit comes first).
         */
        val DEFAULT_FEATURES: List<String> = listOf(
            "Unlimited adaptive games",
            "Full curriculum · 12 chapters",
            "Coach chat · grounded in your games",
            "Opening repertoire drills",
        )

        /**
         * Recommended plan key used by the activity's initial tile
         * selection.  Defaults to "yearly" (matching the design)
         * unless every plan's `isRecommended` is false, in which
         * case we fall back to the first plan.
         */
        fun recommendedPlanKey(plans: List<Plan> = DEFAULT_PLANS): String =
            plans.firstOrNull { it.isRecommended }?.key
                ?: plans.firstOrNull()?.key
                ?: "yearly"
    }
}
