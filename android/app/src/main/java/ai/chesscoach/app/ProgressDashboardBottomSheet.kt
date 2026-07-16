package ai.chesscoach.app

import android.content.Context
import android.graphics.Typeface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.max

/**
 * Full-screen bottom sheet showing the player's profile — the "You"
 * surface: human progress up top, then the coaching dashboard.
 *
 * Sections (post-Elo-removal):
 *  1. Human-progress header — "Level N" + "X XP" hero (cached training
 *     XP, same source as the Home kicker) and a "Recent games ·
 *     N played · M won" stat row from the /player/progress history.
 *  2. Weakness profile — [WeaknessBarChartView] of category scores from the world model.
 *  3. "How the coach sees you" — world-model fields in plain language.
 *     (OPPONENT ELO row suppressed — would otherwise leak the
 *     player's own hidden rating since opponent = rating - ~40.)
 *  4. Coach's plan — most recent decision from SharedPreferences.
 *  5. Training focus — prioritised recommendations from HistoricalAnalysisPipeline.
 *
 * Retired surfaces
 * ----------------
 * The Elo hero cell and confidence row were retired when the
 * user-visible Elo display was hidden, and are now repurposed in
 * place (same view IDs) as the human-progress header above.  The
 * "Rating trend" sparkline section stays fully retired — its views
 * remain in the layout with ``visibility="gone"`` and nothing flips
 * them visible any more (the old ``populateSparkline`` could resurrect
 * the sparkline and re-leak the hidden rating trend); the slot is
 * reserved for a future XP-progress visualisation.
 *
 * Data is fetched from GET /player/progress (Bearer auth).
 * Inject [gameApiClient] before calling [show].
 */
class ProgressDashboardBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the hosting activity before [show] is called. */
    var gameApiClient: GameApiClient? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_progress_dashboard, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val heroLevelBlock      = view.findViewById<LinearLayout>(R.id.heroLevelBlock)
        val txtRating           = view.findViewById<TextView>(R.id.txtRating)
        val txtHeroXp           = view.findViewById<TextView>(R.id.txtHeroXp)
        val statGamesRow        = view.findViewById<LinearLayout>(R.id.statGamesRow)
        val statGamesDivider    = view.findViewById<View>(R.id.statGamesDivider)
        val txtConfidence       = view.findViewById<TextView>(R.id.txtConfidence)
        val weaknessChart       = view.findViewById<WeaknessBarChartView>(R.id.weaknessChart)
        val worldModelContainer = view.findViewById<LinearLayout>(R.id.worldModelContainer)
        val recommendationsList = view.findViewById<LinearLayout>(R.id.recommendationsList)
        val txtNoRecs           = view.findViewById<TextView>(R.id.txtNoRecommendations)
        val txtError            = view.findViewById<TextView>(R.id.txtDashboardError)

        // Human-progress hero — Level / XP from the same SharedPreferences
        // cache that backs the Home kicker (written on every /auth/me
        // round-trip).  Rendered synchronously so the header shows before,
        // and regardless of, the /player/progress fetch below.  Hidden on
        // a fresh install until the first /auth/me lands, matching the
        // Home kicker's behaviour.
        val cachedXp = requireContext()
            .getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
            .getInt(MainActivity.PREF_TRAINING_XP, -1)
        if (cachedXp >= 0) {
            txtRating.text = formatHeroLevel(cachedXp)
            txtHeroXp.text = formatHeroXp(cachedXp)
            heroLevelBlock.visibility = View.VISIBLE
        }

        // ── Coach's plan section (read-only from SharedPreferences) ─────────
        // Populated by GameSummaryBottomSheet on every /game/finish.  The
        // section renders only when there's actually a coach decision to
        // show — first-run / freshly-logged-in players see the existing
        // dashboard sections without an empty coach card stub.
        populateCoachPlanFromPrefs(view)

        val client = gameApiClient ?: run {
            txtError.visibility = View.VISIBLE
            return
        }

        lifecycleScope.launch {
            when (val result = client.getPlayerProgress()) {
                is ApiResult.Success -> {
                    val data = result.data
                    populateGamesRow(statGamesRow, statGamesDivider, txtConfidence, data.history)
                    populateWeaknessChart(weaknessChart, data)
                    populateWorldModel(worldModelContainer, data.current)
                    populateRecommendations(recommendationsList, txtNoRecs, data.analysis)
                }
                else -> {
                    txtError.visibility = View.VISIBLE
                }
            }
        }
    }

    // ── Sections ─────────────────────────────────────────────────────────────

    /**
     * Read the most-recent coach decision from SharedPreferences and
     * surface it as a card above "Training focus".  When there's no
     * decision yet (fresh account / never finished a game), the
     * kicker + card + divider stay hidden and the dashboard reads
     * exactly as it did pre-PR-#172.
     *
     * Source keys are populated by ``GameSummaryBottomSheet``'s
     * persist block — see the matching ``putString`` calls there.
     * The contract is one-way: dashboard never writes, only reads.
     * Logout-time cleanup of these keys is owned by the same
     * SharedPreferences scrub that nukes ``last_rating`` etc.
     */
    private fun populateCoachPlanFromPrefs(view: View) {
        val prefs = requireContext().getSharedPreferences(
            MainActivity.PREFS_NAME,
            Context.MODE_PRIVATE,
        )
        val actionType  = prefs.getString(MainActivity.PREF_LAST_COACH_ACTION_TYPE, null).orEmpty()
        val weakness    = prefs.getString(MainActivity.PREF_LAST_COACH_WEAKNESS, null).orEmpty()
        val reason      = prefs.getString(MainActivity.PREF_LAST_COACH_REASON, null).orEmpty()
        val title       = prefs.getString(MainActivity.PREF_LAST_COACH_TITLE, null).orEmpty()
        val description = prefs.getString(MainActivity.PREF_LAST_COACH_DESCRIPTION, null).orEmpty()

        // Show the section iff we have ANY non-trivial coach content.
        // ``NONE/No trigger`` with empty title+description+weakness is
        // the "controller didn't fire" idle state — no value showing it
        // to the user.
        val hasContent =
            (title.isNotBlank() || description.isNotBlank()) ||
                (actionType.isNotBlank() && actionType != "NONE") ||
                weakness.isNotBlank()

        val kicker  = view.findViewById<TextView>(R.id.txtCoachPlanKicker)
        val card    = view.findViewById<AtriumCardView>(R.id.coachPlanCard)
        val divider = view.findViewById<View>(R.id.coachPlanDivider)

        if (!hasContent) {
            kicker.visibility  = View.GONE
            card.visibility    = View.GONE
            divider.visibility = View.GONE
            return
        }

        val txtAction      = view.findViewById<TextView>(R.id.txtCoachPlanAction)
        val txtWeakness    = view.findViewById<TextView>(R.id.txtCoachPlanWeakness)
        val txtTitle       = view.findViewById<TextView>(R.id.txtCoachPlanTitle)
        val txtDescription = view.findViewById<TextView>(R.id.txtCoachPlanDescription)
        val txtReason      = view.findViewById<TextView>(R.id.txtCoachPlanReason)

        txtAction.text = actionVerdictLabel(actionType)

        if (weakness.isNotBlank()) {
            txtWeakness.text = "FOCUS · ${weakness.uppercase()}"
            txtWeakness.visibility = View.VISIBLE
        } else {
            txtWeakness.visibility = View.GONE
        }

        if (title.isNotBlank()) {
            txtTitle.text = title
            txtTitle.visibility = View.VISIBLE
        } else {
            txtTitle.visibility = View.GONE
        }

        if (description.isNotBlank()) {
            txtDescription.text = description
            txtDescription.visibility = View.VISIBLE
        } else {
            txtDescription.visibility = View.GONE
        }

        if (reason.isNotBlank()) {
            txtReason.text = reason
            txtReason.visibility = View.VISIBLE
        } else {
            txtReason.visibility = View.GONE
        }

        kicker.visibility  = View.VISIBLE
        card.visibility    = View.VISIBLE
        divider.visibility = View.VISIBLE
    }

    /**
     * Map raw ``CoachAction.type`` strings to readable card kickers.
     * Mirrors ``GameSummaryBottomSheet.actionBadgeLabel`` so the same
     * action surfaces with the same label across both screens.
     */
    private fun actionVerdictLabel(actionType: String): String = when (actionType.uppercase()) {
        "DRILL"       -> "DRILL"
        "PUZZLE"      -> "PUZZLE"
        "REFLECT"     -> "REFLECT"
        "PLAN_UPDATE" -> "PLAN UPDATE"
        "CELEBRATE"   -> "CELEBRATE"
        "NONE", ""    -> "COACH"
        else          -> "COACH"
    }

    /**
     * "Recent games · N played · M won" stat row.  Sourced from the
     * /player/progress history window (newest-first, server-capped at
     * 20 rows) — no endpoint returns lifetime totals, so the label
     * says "Recent" honestly rather than implying an all-time count.
     * The divider under the row flips visible with it so a fetch
     * failure never leaves an orphan hairline above WEAKNESS PROFILE.
     */
    private fun populateGamesRow(
        row: View,
        divider: View,
        txtGames: TextView,
        history: List<ProgressHistoryItem>,
    ) {
        txtGames.text = formatGamesSummary(history)
        row.visibility = View.VISIBLE
        divider.visibility = View.VISIBLE
    }

    private fun populateWeaknessChart(
        chart: WeaknessBarChartView,
        data: PlayerProgressResponse,
    ) {
        // Build entries from category_scores; annotate with recommendation priority.
        val priorityMap = data.analysis.recommendations.associate { it.category to it.priority }

        val labelFor = mapOf(
            "tactical_vision"      to "Tactics",
            "opening_preparation"  to "Opening",
            "endgame_technique"    to "Endgame",
            "positional_play"      to "Position",
        )

        val entries = data.analysis.categoryScores
            .entries
            .sortedByDescending { it.value }
            .map { (cat, score) ->
                WeaknessBarChartView.Entry(
                    label    = labelFor[cat] ?: cat,
                    value    = score,
                    priority = priorityMap[cat] ?: "",
                )
            }

        // Fall back to raw skill_vector when no pipeline data available.
        val finalEntries = if (entries.isEmpty()) {
            data.current.skillVector.entries
                .sortedByDescending { it.value }
                .map { (k, v) -> WeaknessBarChartView.Entry(label = k, value = v) }
        } else {
            entries
        }

        chart.setEntries(finalEntries)
    }

    private fun populateWorldModel(
        container: LinearLayout,
        current: ProgressCurrentDto,
    ) {
        val tierLabel = when (current.tier) {
            "beginner"     -> "Beginner — keep it simple"
            "intermediate" -> "Intermediate — building concepts"
            "advanced"     -> "Advanced — deep analysis"
            else           -> current.tier
        }

        val styleLabel = when (current.teachingStyle) {
            "simple"       -> "Simple explanations, 1 concept at a time"
            "intermediate" -> "Balanced depth, some variations shown"
            "advanced"     -> "Full analysis, all variations"
            else           -> current.teachingStyle
        }

        // OPPONENT ELO was removed when the user-visible Elo rating was
        // hidden from the UI — exposing the matched-opponent rating
        // here would leak the player's own (now-hidden) rating since
        // the matcher derives the opponent from rating - ~40.  The
        // adaptive-difficulty selection itself still happens
        // internally; it is just no longer displayed.
        val rows = listOf(
            "TIER"          to tierLabel,
            "COACH STYLE"   to styleLabel,
            "DEPTH"         to "%.0f%%".format(current.explanationDepth * 100),
            "COMPLEXITY"    to "%.0f%%".format(current.conceptComplexity * 100),
        )

        rows.forEach { (label, value) ->
            container.addView(buildWorldModelRow(label, value))
        }
    }

    private fun populateRecommendations(
        list: LinearLayout,
        txtNone: TextView,
        analysis: ProgressAnalysisDto,
    ) {
        if (analysis.recommendations.isEmpty()) {
            txtNone.visibility = View.VISIBLE
            return
        }
        analysis.recommendations.forEach { rec ->
            list.addView(buildRecommendationRow(rec))
        }
    }

    // ── Row builders ─────────────────────────────────────────────────────────

    private fun buildWorldModelRow(label: String, value: String): View {
        // Atrium re-skin: mono dim kicker on the left, mono ink value
        // on the right.  Mono stays for telemetry rows per Atrium's
        // "Numerics: JetBrains Mono" role; the colour shift to the
        // dim/ink tokens lines up with the rest of the dashboard.
        val ctx = requireContext()
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 8, 0, 8)

            addView(TextView(ctx).apply {
                text = label
                textSize = 11f
                setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_dim))
                typeface = Typeface.MONOSPACE
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            })

            addView(TextView(ctx).apply {
                text = value
                textSize = 12f
                setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_ink))
                typeface = Typeface.MONOSPACE
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 2f)
            })
        }
    }

    private fun buildRecommendationRow(rec: ProgressRecommendation): View {
        // Atrium two-tone signal — mirrors WeaknessBarChartView.priorityColor
        // so high-severity recommendations read amber (warning role) and
        // low-severity read cyan (improving / player-side).  Token reads
        // so bright mode flips them via values-notnight/colors.xml.
        val ctx = requireContext()
        val priorityColor = when (rec.priority) {
            "high"   -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_amber)
            "medium" -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_amber_cc)
            else     -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_cyan)
        }

        val categoryLabel = rec.category
            .replace("_", " ")
            .split(" ")
            .joinToString(" ") { it.replaceFirstChar(Char::uppercase) }

        // Atrium colours: priority kicker in the two-tone signal
        // (amber for high/medium, cyan for low), category in ink,
        // rationale in muted ink.  Mono kept for kickers; category
        // and rationale stay mono here for compact-list density —
        // promoting them to Cormorant italic would inflate row
        // height and crowd the small-screen bottom sheet.
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 10, 0, 10)

            addView(LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL

                addView(TextView(ctx).apply {
                    text = rec.priority.uppercase()
                    textSize = 9f
                    setTextColor(priorityColor)
                    typeface = Typeface.MONOSPACE
                    setPadding(0, 0, 12, 0)
                })

                addView(TextView(ctx).apply {
                    text = categoryLabel
                    textSize = 13f
                    setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_ink))
                    typeface = Typeface.MONOSPACE
                })
            })

            addView(TextView(ctx).apply {
                text = rec.rationale
                textSize = 11f
                setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_muted))
                typeface = Typeface.MONOSPACE
                setPadding(0, 4, 0, 0)
            })
        }
    }

    companion object {

        // ── Pure display helpers — testable without Android framework ─────────

        /**
         * "Level N" hero line.  Reuses [HomeActivity.XP_PER_LEVEL] and
         * clamps like [HomeActivity.formatXpKicker] (negatives / fresh
         * accounts read "Level 1") so the profile hero and the Home
         * kicker can never disagree on the level curve.
         */
        fun formatHeroLevel(xp: Int): String {
            val safeXp = max(0, xp)
            return "Level ${max(1, safeXp / HomeActivity.XP_PER_LEVEL + 1)}"
        }

        /** "340 XP" kicker under the hero level; clamps negatives to 0. */
        fun formatHeroXp(xp: Int): String = "${max(0, xp)} XP"

        /**
         * "N played · M won" from the /player/progress history window.
         * Only "win" rows count as won — draws and losses contribute to
         * the played count alone.
         */
        fun formatGamesSummary(history: List<ProgressHistoryItem>): String {
            val won = history.count { it.result.equals("win", ignoreCase = true) }
            return "${history.size} played · $won won"
        }
    }
}
