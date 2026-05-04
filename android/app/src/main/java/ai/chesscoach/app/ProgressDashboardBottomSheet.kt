package ai.chesscoach.app

import android.graphics.Color
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

/**
 * Full-screen bottom sheet showing the player's progress dashboard.
 *
 * Sections:
 *  1. Rating sparkline — last 10 rated games in chronological order.
 *  2. Rating / confidence summary row.
 *  3. Weakness profile — [WeaknessBarChartView] of category scores from the world model.
 *  4. "How the coach sees you" — world-model fields in plain language.
 *  5. Training focus — prioritised recommendations from HistoricalAnalysisPipeline.
 *
 * Data is fetched from GET /player/progress (Bearer auth).
 * Inject [gameApiClient] before calling [show].
 */
class ProgressDashboardBottomSheet : BottomSheetDialogFragment() {

    /** Injected by [MainActivity] before [show] is called. */
    var gameApiClient: GameApiClient? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_progress_dashboard, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val sparkline           = view.findViewById<RatingSparklineView>(R.id.progressSparkline)
        val txtNoRatingHistory  = view.findViewById<TextView>(R.id.txtNoRatingHistory)
        val txtRating           = view.findViewById<TextView>(R.id.txtRating)
        val txtConfidence       = view.findViewById<TextView>(R.id.txtConfidence)
        val weaknessChart       = view.findViewById<WeaknessBarChartView>(R.id.weaknessChart)
        val worldModelContainer = view.findViewById<LinearLayout>(R.id.worldModelContainer)
        val recommendationsList = view.findViewById<LinearLayout>(R.id.recommendationsList)
        val txtNoRecs           = view.findViewById<TextView>(R.id.txtNoRecommendations)
        val txtError            = view.findViewById<TextView>(R.id.txtDashboardError)

        val client = gameApiClient ?: run {
            txtError.visibility = View.VISIBLE
            return
        }

        lifecycleScope.launch {
            when (val result = client.getPlayerProgress()) {
                is ApiResult.Success -> {
                    val data = result.data
                    populateRatingRow(txtRating, txtConfidence, data.current)
                    populateSparkline(sparkline, txtNoRatingHistory, data.history)
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

    private fun populateRatingRow(
        txtRating: TextView,
        txtConfidence: TextView,
        current: ProgressCurrentDto,
    ) {
        txtRating.text = "%.0f".format(current.rating)
        val confPct = "${(current.confidence * 100).toInt()}%"
        txtConfidence.text = confPct
    }

    private fun populateSparkline(
        sparkline: RatingSparklineView,
        txtNone: TextView,
        history: List<ProgressHistoryItem>,
    ) {
        val ratings = history.take(10).reversed().mapNotNull { it.ratingAfter }
        if (ratings.size >= 2) {
            sparkline.setRatings(ratings)
            sparkline.visibility = View.VISIBLE
        } else {
            txtNone.visibility = View.VISIBLE
        }
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

        val rows = listOf(
            "TIER"          to tierLabel,
            "OPPONENT ELO"  to "${current.opponentElo}",
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
        // low-severity read cyan (improving / player-side).
        val priorityColor = when (rec.priority) {
            "high"   -> Color.parseColor("#FFC069") // atrium_accent_amber
            "medium" -> Color.parseColor("#CCFFC069") // amber @ 80%
            else     -> Color.parseColor("#4FD9E5") // atrium_accent_cyan
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
        val ctx = requireContext()
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
}
