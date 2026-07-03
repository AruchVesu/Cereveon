package ai.chesscoach.app

import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.PurchasesUpdatedListener
import com.android.billingclient.api.QueryProductDetailsParams
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Paywall (handoff screen #11).
 *
 * Reached from SettingsBottomSheet → "Upgrade · Premium" chevron row.
 *
 * Purchase flow (Play Billing → server verify → Pro)
 * --------------------------------------------------
 *  1. Begin → connect [billingClient], query the selected plan's
 *     subscription product ([PLAY_PRODUCT_IDS]), launch the Play
 *     purchase sheet.
 *  2. [purchasesUpdatedListener] receives the PURCHASED result and
 *     posts the purchase token to POST /billing/google/verify — the
 *     SERVER is the entitlement authority; a local purchase result is
 *     never trusted on its own (docs/API_CONTRACTS.md §36).
 *  3. Only on a verified `plan == "pro"` ([verifyOutcome]): acknowledge
 *     the purchase with Play, cache the plan locally
 *     ([PREF_PLAYER_PLAN]), and finish into the Pro state.
 *  4. Any verify failure keeps the paywall open AND leaves the purchase
 *     unacknowledged — Play auto-refunds unacknowledged purchases, so a
 *     dead server can never silently keep the user's money.  Reopening
 *     the paywall retries pending verification via the purchases-updated
 *     listener on the next purchase attempt.
 *
 * The static plan catalogue ([DEFAULT_PLANS] / [DEFAULT_FEATURES] /
 * [recommendedPlanKey]) is unchanged from the scaffold pass — display
 * pricing stays design-driven for now; the Play product catalogue only
 * decides what is PURCHASED, keyed by [PLAY_PRODUCT_IDS].
 */
class PaywallActivity : AppCompatActivity() {

    private var selectedPlanKey: String = "yearly"
    private lateinit var monthlyTile: FrameLayout
    private lateinit var yearlyTile: FrameLayout
    private lateinit var monthlyPrice: TextView
    private lateinit var yearlyPrice: TextView
    private lateinit var monthlySub: TextView
    private lateinit var yearlySub: TextView

    private val authRepo: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    private val billingApi: BillingApiClient by lazy {
        HttpBillingApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
            tokenProvider = { authRepo.getToken() },
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
    }

    /**
     * Play Billing results arrive here — including purchases completed
     * in a previous session that Play redelivers on reconnect, which is
     * what retries a purchase whose server verify failed last time.
     */
    private val purchasesUpdatedListener = PurchasesUpdatedListener { result, purchases ->
        when {
            result.responseCode == BillingClient.BillingResponseCode.OK && purchases != null ->
                purchases.forEach(::handlePurchase)
            result.responseCode == BillingClient.BillingResponseCode.USER_CANCELED ->
                Unit // deliberate dismissal — no toast noise
            else ->
                toastOnUi("Purchase did not complete (code ${result.responseCode})")
        }
    }

    private lateinit var billingClient: BillingClient

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_paywall)

        billingClient = BillingClient.newBuilder(this)
            .setListener(purchasesUpdatedListener)
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build()
            )
            .build()

        // Theme runs edge-to-edge; without this listener the bottom
        // "Begin · 7 days free" / "Maybe later" footer would render
        // under the system gesture / nav bar.
        val footer = findViewById<LinearLayout>(R.id.paywallFooter)
        val footerBasePaddingBottom = footer.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(footer) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(bottom = footerBasePaddingBottom + bars.bottom)
            insets
        }

        renderFeatureBullets(findViewById(R.id.paywallFeatures))

        monthlyTile  = findViewById(R.id.paywallPlanMonthly)
        yearlyTile   = findViewById(R.id.paywallPlanYearly)
        monthlyPrice = findViewById(R.id.paywallPlanMonthlyPrice)
        yearlyPrice  = findViewById(R.id.paywallPlanYearlyPrice)
        monthlySub   = findViewById(R.id.paywallPlanMonthlySub)
        yearlySub    = findViewById(R.id.paywallPlanYearlySub)

        // DEFAULT_PLANS is the single source for the tile copy — the XML
        // values are pre-bind placeholders.  What gets BILLED is the Play
        // Console product behind PLAY_PRODUCT_IDS; these labels must be
        // kept in lock-step with the prices configured there (pinned by
        // PaywallActivityTest's launch-pricing test).
        DEFAULT_PLANS.firstOrNull { it.key == "monthly" }?.let {
            monthlyPrice.text = it.price
            monthlySub.text = it.sub
        }
        DEFAULT_PLANS.firstOrNull { it.key == "yearly" }?.let {
            yearlyPrice.text = it.price
            yearlySub.text = it.sub
        }

        monthlyTile.setOnClickListener { selectPlan("monthly") }
        yearlyTile.setOnClickListener { selectPlan("yearly") }
        // Initial state matches the design — yearly active by default.
        selectPlan(selectedPlanKey)

        findViewById<Button>(R.id.btnPaywallBegin).setOnClickListener {
            startPurchase(productIdFor(selectedPlanKey))
        }
        findViewById<TextView>(R.id.btnPaywallMaybeLater).setOnClickListener {
            finish()
        }
    }

    override fun onDestroy() {
        if (this::billingClient.isInitialized) {
            billingClient.endConnection()
        }
        super.onDestroy()
    }

    // ── Play Billing flow ────────────────────────────────────────────

    private fun startPurchase(productId: String) {
        if (billingClient.isReady) {
            queryAndLaunch(productId)
            return
        }
        billingClient.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                    queryAndLaunch(productId)
                } else {
                    // No Play services / not signed in / emulator without
                    // Play — the paywall stays open and harmless.
                    toastOnUi("Google Play billing unavailable (code ${result.responseCode})")
                }
            }

            override fun onBillingServiceDisconnected() {
                // Next Begin tap reconnects; no retry loop needed here.
            }
        })
    }

    private fun queryAndLaunch(productId: String) {
        val params = QueryProductDetailsParams.newBuilder()
            .setProductList(
                listOf(
                    QueryProductDetailsParams.Product.newBuilder()
                        .setProductId(productId)
                        .setProductType(BillingClient.ProductType.SUBS)
                        .build()
                )
            )
            .build()
        billingClient.queryProductDetailsAsync(params) { result, productDetailsList ->
            val details = productDetailsList.firstOrNull()
            if (result.responseCode != BillingClient.BillingResponseCode.OK || details == null) {
                toastOnUi("Plan not available right now — try again shortly")
                return@queryProductDetailsAsync
            }
            // Subscriptions always carry at least one offer; the base
            // plan's token is the first entry when no targeted offer
            // applies.  A missing token means the Play product is
            // misconfigured (not a client bug) — fail soft.
            val offerToken = details.subscriptionOfferDetails?.firstOrNull()?.offerToken
            if (offerToken == null) {
                toastOnUi("Plan not available right now — try again shortly")
                return@queryProductDetailsAsync
            }
            val flowParams = BillingFlowParams.newBuilder()
                .setProductDetailsParamsList(
                    listOf(
                        BillingFlowParams.ProductDetailsParams.newBuilder()
                            .setProductDetails(details)
                            .setOfferToken(offerToken)
                            .build()
                    )
                )
                .build()
            runOnUiThread { billingClient.launchBillingFlow(this, flowParams) }
        }
    }

    private fun handlePurchase(purchase: Purchase) {
        if (purchase.purchaseState != Purchase.PurchaseState.PURCHASED) {
            // PENDING (e.g. cash top-up pending) — Play redelivers via the
            // listener once it completes; nothing to verify yet.
            return
        }
        val productId = purchase.products.firstOrNull() ?: return
        lifecycleScope.launch {
            val result = billingApi.verifyGooglePurchase(purchase.purchaseToken, productId)
            when (verifyOutcome(result)) {
                VerifyOutcome.PRO_ACTIVATED -> {
                    acknowledgeIfNeeded(purchase)
                    cachePlan("pro")
                    toastOnUi("Premium active — welcome aboard")
                    finish()
                }
                VerifyOutcome.KEEP_PAYWALL -> {
                    // Purchase stays UNACKNOWLEDGED on purpose: if the
                    // server stays unreachable, Play refunds it — the
                    // user is never charged for an entitlement the
                    // server never granted.
                    Log.w(TAG, "verify failed for $productId: $result")
                    toastOnUi(
                        "Could not confirm the purchase with the coach server — " +
                            "it will be retried, you won't be double-charged"
                    )
                }
            }
        }
    }

    private fun acknowledgeIfNeeded(purchase: Purchase) {
        if (purchase.isAcknowledged) return
        val params = AcknowledgePurchaseParams.newBuilder()
            .setPurchaseToken(purchase.purchaseToken)
            .build()
        billingClient.acknowledgePurchase(params) { result ->
            // Best-effort: the SERVER verdict granted the plan; a failed
            // acknowledge just means Play redelivers the purchase and we
            // re-acknowledge on the next listener pass.
            if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                Log.w(TAG, "acknowledgePurchase failed (code ${result.responseCode})")
            }
        }
    }

    private fun cachePlan(plan: String) {
        getSharedPreferences(MainActivity.PREFS_NAME, MODE_PRIVATE).edit()
            .putString(PREF_PLAYER_PLAN, plan)
            .apply()
    }

    private fun toastOnUi(message: String) {
        runOnUiThread { Toast.makeText(this, message, Toast.LENGTH_SHORT).show() }
    }

    // ── Static UI scaffolding (unchanged from the scaffold pass) ─────

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

    /** Terminal decision after a server verify — see [verifyOutcome]. */
    enum class VerifyOutcome { PRO_ACTIVATED, KEEP_PAYWALL }

    companion object {
        private const val TAG = "PaywallActivity"

        /**
         * SharedPreferences key (in [MainActivity.PREFS_NAME]) caching
         * the last server-confirmed plan ("free" / "pro").  Written by
         * this activity after a verified purchase; read by the limit /
         * upgrade UI (client-reaction follow-up).  A UI cache only —
         * the server re-decides entitlement on every metered call.
         */
        const val PREF_PLAYER_PLAN = "player_plan"

        /**
         * Paywall plan key → Play Console product id.  Must stay in
         * lock-step with the server's `KNOWN_PRODUCTS` in
         * `llm/seca/billing/router.py` (both products grant plan "pro")
         * and with the products configured in the Play Console.
         */
        val PLAY_PRODUCT_IDS: Map<String, String> = mapOf(
            "monthly" to "pro_monthly",
            "yearly" to "pro_yearly",
        )

        /**
         * The Play product to purchase for a paywall plan key.  Unknown
         * keys fall back to the monthly product — defensive only; the
         * activity's click listeners can only produce catalogue keys
         * (pinned by [PaywallActivityTest]).
         */
        fun productIdFor(planKey: String): String =
            PLAY_PRODUCT_IDS[planKey] ?: PLAY_PRODUCT_IDS.getValue("monthly")

        /**
         * Decide the paywall's terminal state from a verify response.
         * ONLY an [ApiResult.Success] whose body says `plan == "pro"`
         * activates — every error (402 not entitled, 502/503 upstream,
         * network, timeout) and every non-pro body keeps the paywall
         * open and the purchase unacknowledged.  Static + framework-free
         * so the host-JVM test suite pins the transition table.
         */
        fun verifyOutcome(result: ApiResult<BillingVerifyResponse>): VerifyOutcome =
            if (result is ApiResult.Success && result.data.plan == "pro") {
                VerifyOutcome.PRO_ACTIVATED
            } else {
                VerifyOutcome.KEEP_PAYWALL
            }

        /**
         * Canonical plan-tile copy, bound to the tiles in [onCreate].
         * Lifted to the companion so unit tests can verify the shape
         * without launching the activity; the "yearly" entry is marked
         * recommended (drives the initial active-tile selection).
         *
         * LAUNCH PRICING (2026-07): €9.99/month; yearly €71.99 (= €6 a
         * month, ~40% off).  Chosen against the MEASURED unit costs —
         * a fully-coached game ≈ $0.0033 in DeepSeek tokens, so a
         * heavy Pro user costs well under €1/month (≥95% gross margin
         * after ~20% VAT + Play's 15% fee).  These labels are DISPLAY
         * copy: what gets billed is the Play Console product behind
         * [PLAY_PRODUCT_IDS] — change both together, and let Play's
         * per-country price templates localise the actual charge.
         */
        val DEFAULT_PLANS: List<Plan> = listOf(
            Plan(
                key = "monthly",
                title = "Monthly",
                price = "€9.99",
                sub = "per month",
                isRecommended = false,
            ),
            Plan(
                key = "yearly",
                title = "Yearly",
                price = "€71.99",
                sub = "€6 / month",
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
