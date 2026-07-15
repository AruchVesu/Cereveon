package ai.chesscoach.app

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.google.android.material.bottomsheet.BottomSheetDialogFragment

/**
 * Cereveon · Atrium · Study-plan week overview (study-plan phase 2b).
 *
 * Renders the whole weekly curriculum at a glance — the aggregate
 * dominant-weakness focus ([CoachPlanResponse.anchorCategory]), the LLM
 * coach verdict, and the three spaced-repetition days (offset 0 / 3 / 7)
 * each marked **Today / Done / Locked** — with a primary CTA that
 * launches the existing [TodaysDrillBottomSheet] for the currently-due
 * puzzle ([CoachPlanResponse.todayPuzzle]).
 *
 * Pure renderer
 * -------------
 * HomeActivity passes the already-fetched [CoachPlanResponse] as a JSON
 * string, so this sheet does **no network I/O**.  The "Start today's
 * drill" CTA dismisses this sheet and shows the drill, so only one sheet
 * is visible at a time — there is no stacked-dialog lifecycle / stale
 * refresh problem.  After the drill advances the plan (it calls
 * `completePlanPuzzle` itself), the user lands back on Home, whose
 * `onResume` re-polls `/coach/plan/today`; reopening this overview
 * therefore always renders fresh state.
 *
 * A secondary "Practice puzzles" CTA opens the standalone
 * [PuzzleTrainerBottomSheet] (endless Lichess-fed stream) with the same
 * dismiss-then-show pattern — plan drills and free practice share the
 * Puzzles tab without stacking sheets.
 *
 * Args
 * ----
 * Single bundle extra [ARG_PLAN_JSON] — the serialized
 * [CoachPlanResponse].  See [newInstance].
 */
class StudyPlanOverviewBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host before [show]; forwarded to the drill sheet. */
    var gameApiClient: GameApiClient? = null

    private var plan: CoachPlanResponse? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_study_plan_overview, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val planJson = requireArguments().getString(ARG_PLAN_JSON, "")
        val decoded = try {
            // Explicit-serializer form needs no kotlinx.serialization import
            // (the reified extension would), keeping this decode-only file's
            // import list clean for ktlint.
            ApiJson.decodeFromString(CoachPlanResponse.serializer(), planJson)
        } catch (_: Exception) {
            // Should never happen — HomeActivity encodes a valid response
            // before opening us.  If it somehow does, fail closed rather
            // than render a half-empty sheet.
            dismiss()
            return
        }
        plan = decoded

        view.findViewById<TextView>(R.id.overviewFocus).text = formatFocus(decoded)

        val verdictView = view.findViewById<TextView>(R.id.overviewVerdict)
        if (decoded.verdict.isNotBlank()) {
            verdictView.text = decoded.verdict
            verdictView.visibility = View.VISIBLE
        } else {
            verdictView.visibility = View.GONE
        }

        view.findViewById<TextView>(R.id.overviewProgress).text =
            formatProgress(decoded.days, decoded.totalDays)

        bindDayRows(view, decoded.days)
        bindCta(view, decoded)

        // Secondary entry into the standalone puzzle trainer — the
        // endless Lichess-fed practice stream.  Same dismiss-then-show
        // pattern as the drill CTA so only one sheet is up at a time.
        view.findViewById<Button>(R.id.overviewPracticeButton).setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            val sheet = PuzzleTrainerBottomSheet()
            sheet.gameApiClient = gameApiClient
            val fm = parentFragmentManager
            dismiss()
            sheet.show(fm, "PuzzleTrainerBottomSheet")
        }

        view.findViewById<Button>(R.id.overviewCloseButton).setOnClickListener { dismiss() }
    }

    private fun bindDayRows(view: View, days: List<PlanDayDto>) {
        val rows = listOf(
            Triple(R.id.overviewDay1Row, R.id.overviewDay1Label, R.id.overviewDay1Status),
            Triple(R.id.overviewDay2Row, R.id.overviewDay2Label, R.id.overviewDay2Status),
            Triple(R.id.overviewDay3Row, R.id.overviewDay3Label, R.id.overviewDay3Status),
        )
        rows.forEachIndexed { index, (rowId, labelId, statusId) ->
            val row = view.findViewById<LinearLayout>(rowId)
            val day = days.getOrNull(index)
            if (day == null) {
                // Legacy server response with no days[] — hide the empty slot.
                row.visibility = View.GONE
                return@forEachIndexed
            }
            row.visibility = View.VISIBLE
            view.findViewById<TextView>(labelId).text =
                formatDayLabel(dayNumber(day.dayOffset), day.sourceType)
            val status = view.findViewById<TextView>(statusId)
            status.text = statusText(day)
            status.setTextColor(ContextCompat.getColor(requireContext(), statusColorRes(day)))
        }
    }

    private fun bindCta(view: View, response: CoachPlanResponse) {
        val cta = view.findViewById<Button>(R.id.overviewStartButton)
        val puzzle = response.todayPuzzle
        if (puzzle == null) {
            // Nothing due right now — either the week is complete or the
            // next day hasn't unlocked yet.  Hide the CTA; the day rows
            // already communicate the state.
            cta.visibility = View.GONE
            return
        }
        cta.visibility = View.VISIBLE
        cta.text = formatCtaLabel(dayNumber(puzzle.dayOffset))
        cta.setOnClickListener {
            if (parentFragmentManager.isStateSaved) return@setOnClickListener
            val sheet = TodaysDrillBottomSheet.newInstance(
                planId = response.planId,
                dayOffset = puzzle.dayOffset,
                totalDays = response.totalDays,
                theme = response.theme,
                verdict = response.verdict,
                fen = puzzle.fen,
                expectedMoveUci = puzzle.expectedMoveUci,
                solutionLineUci = puzzle.solutionLineUci,
            )
            sheet.gameApiClient = gameApiClient
            // Capture the FM before dismissing — show the drill on the
            // same (activity) manager so only one sheet is up at a time.
            val fm = parentFragmentManager
            dismiss()
            sheet.show(fm, "TodaysDrillBottomSheet")
        }
    }

    companion object {
        private const val ARG_PLAN_JSON = "plan_json"

        private val STATUS_TODAY_COLOR = R.color.atrium_accent_cyan
        private val STATUS_DONE_COLOR = R.color.atrium_muted
        private val STATUS_LOCKED_COLOR = R.color.atrium_dim

        fun newInstance(planJson: String): StudyPlanOverviewBottomSheet =
            StudyPlanOverviewBottomSheet().apply {
                arguments = Bundle().apply { putString(ARG_PLAN_JSON, planJson) }
            }

        /**
         * The big focus title — the player's aggregate weakness mapped to
         * a friendly noun ([formatCategory]).  Falls back to the day-0
         * mistake's own theme, then to a neutral default, so the title is
         * never blank.  Pure — unit-testable without a view.
         */
        fun formatFocus(response: CoachPlanResponse): String {
            val byCategory = formatCategory(response.anchorCategory)
            if (byCategory.isNotEmpty()) return byCategory
            val byTheme = TodaysDrillBottomSheet.prettyTheme(response.theme)
            if (byTheme.isNotEmpty()) return byTheme
            return "This week"
        }

        /**
         * Map an aggregate [CoachPlanResponse.anchorCategory] (one of the
         * four MistakeCategory values) to a friendly focus noun.  Returns
         * "" for null / generic / unknown so the caller can fall back.
         */
        fun formatCategory(category: String?): String = when (category?.trim()?.lowercase()) {
            "tactical_vision" -> "Tactics"
            "endgame_technique" -> "Endgames"
            "opening_preparation" -> "Openings"
            "positional_play" -> "Strategy"
            else -> ""
        }

        /** 1-based day in the plan (offset 0→1, 3→2, 7→3). */
        fun dayNumber(dayOffset: Int): Int = when (dayOffset) {
            0 -> 1
            3 -> 2
            7 -> 3
            else -> 1
        }

        /**
         * Row label: "Day N · Replay your mistake" for the original day-0
         * position, "Day N · Practice" for the library practice puzzles.
         */
        fun formatDayLabel(dayNumber: Int, sourceType: String): String {
            val kind =
                if (sourceType.trim().lowercase() == "original") "Replay your mistake"
                else "Practice"
            return "Day $dayNumber · $kind"
        }

        /** Status word for one day: Done / Today / Locked. */
        fun statusText(day: PlanDayDto): String = when {
            day.completed -> "Done"
            day.isDue -> "Today"
            else -> "Locked"
        }

        /** Status colour resource: cyan = today (actionable), muted = done, dim = locked. */
        fun statusColorRes(day: PlanDayDto): Int = when {
            day.completed -> STATUS_DONE_COLOR
            day.isDue -> STATUS_TODAY_COLOR
            else -> STATUS_LOCKED_COLOR
        }

        /**
         * "Day N of M" progress, or "Week complete" once every day is
         * solved.  N is the count of completed days + 1 (the day you're
         * up to), capped at M.  Pure — unit-testable.
         */
        fun formatProgress(days: List<PlanDayDto>, totalDays: Int): String {
            val completed = days.count { it.completed }
            if (totalDays > 0 && completed >= totalDays) return "Week complete"
            return "Day ${completed + 1} of $totalDays"
        }

        /** Primary CTA label for the currently-due day. */
        fun formatCtaLabel(dayNumber: Int): String = "Start day $dayNumber"
    }
}
