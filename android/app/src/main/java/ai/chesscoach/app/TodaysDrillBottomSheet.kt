package ai.chesscoach.app

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Today's drill bottom sheet (study-plan phase 4).
 *
 * Launched from [HomeActivity]'s ``TodaysDrillCard`` when
 * /coach/plan/today returns a non-null ``today_puzzle``.  Drills the
 * user on the currently-due puzzle in their per-mistake study plan
 * (day 0 = the original mistake position; days 3 and 7 = theme-matched
 * library variants).
 *
 * Flow per attempt
 * ----------------
 *  1.  User taps & drags a piece on [ChessBoardView] → fires
 *      ``onMovePlayed(fr, fc, tr, tc)``.
 *  2.  Board is locked (``isInteractive=false``) and the status text
 *      flips to "Checking...".
 *  3.  Activity calls ``POST /training/verify-replay`` with the FEN
 *      and the UCI of the attempted move.  Server runs Stockfish.
 *  4a. ``isCorrect=true`` → activity calls ``POST /training/solve``
 *      with ``source_type=mistake_replay`` and
 *      ``source_ref=plan_<plan_id>:day_<day_offset>``.  Server
 *      credits +10 XP; activity toasts "+10 XP", updates
 *      ``PREF_TRAINING_XP`` so Home re-renders, and dismisses.
 *  4b. ``isCorrect=false`` → status flips to "Not quite, try again"
 *      (amber), the board's FEN is reset to the puzzle position,
 *      and ``isInteractive`` is re-enabled.  No XP penalty; the user
 *      can retry indefinitely (matches the [MistakeReplayBottomSheet]
 *      UX).
 *
 * The verify + solve flow is intentionally identical to
 * [MistakeReplayBottomSheet] — they both terminate at
 * ``/training/solve`` with the same ``source_type="mistake_replay"``
 * so the ``(player, source_type, source_ref)`` dedup triple keeps each
 * individual day-N puzzle credit-once.  Only the source_ref shape
 * differs:
 *
 * * Mistake replay (post-game sheet):
 *     ``event_<event_id>:move_<move_number>``
 * * Today's drill (this sheet):
 *     ``plan_<plan_id>:day_<day_offset>``
 *
 * Args
 * ----
 * Carried as bundle extras.  See [newInstance] for the canonical
 * construction path used by [HomeActivity.fetchAndPopulateTodaysDrill].
 */
class TodaysDrillBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host activity before [show]; required for the verify + solve calls. */
    var gameApiClient: GameApiClient? = null

    private lateinit var board: ChessBoardView
    private lateinit var statusView: TextView

    private var fen: String = ""
    private var sourceRef: String = ""

    // Plan coordinates for the completion call after a verified solve —
    // advances the study plan (day 0 -> 3 -> 7) so the week progresses.
    private var planId: String = ""
    private var dayOffset: Int = 0

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_todays_drill, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val args = requireArguments()
        fen = args.getString(ARG_FEN, "")
        planId = args.getString(ARG_PLAN_ID, "")
        dayOffset = args.getInt(ARG_DAY_OFFSET, 0)
        val totalDays = args.getInt(ARG_TOTAL_DAYS, 3)
        val theme = args.getString(ARG_THEME, "generic")
        val verdict = args.getString(ARG_VERDICT, "")
        sourceRef = formatSourceRef(planId = planId, dayOffset = dayOffset)

        view.findViewById<TextView>(R.id.todaysDrillKicker).text =
            formatKicker(dayOffset = dayOffset, totalDays = totalDays, theme = theme)

        val verdictView = view.findViewById<TextView>(R.id.todaysDrillVerdict)
        if (verdict.isNotBlank()) {
            verdictView.text = verdict
            verdictView.visibility = View.VISIBLE
        } else {
            // Verdict empty (LLM unreachable / validator-rejected) —
            // hide the line cleanly rather than render an empty
            // paragraph between the title and the board.
            verdictView.visibility = View.GONE
        }

        board = view.findViewById(R.id.todaysDrillBoard)
        statusView = view.findViewById(R.id.todaysDrillStatus)

        board.setFEN(fen)
        board.isInteractive = true
        board.onMovePlayed = { fr, fc, tr, tc -> handleAttempt(fr, fc, tr, tc) }

        view.findViewById<Button>(R.id.todaysDrillCloseButton).setOnClickListener {
            dismiss()
        }
    }

    private fun handleAttempt(fr: Int, fc: Int, tr: Int, tc: Int) {
        // Lock the board the moment the move lands so the user can't
        // fire a second attempt while the first is in flight.  Wrong-
        // move recovery re-enables ``isInteractive``; correct-move
        // path dismisses the sheet.
        board.isInteractive = false

        val moveResult = board.applyMove(fr, fc, tr, tc)
        if (moveResult == MoveResult.FAILED) {
            // Illegal in this position (e.g. moves into check).
            board.isInteractive = true
            return
        }

        val moveUci = MistakeReplayBottomSheet.rowColToUci(fr, fc) +
            MistakeReplayBottomSheet.rowColToUci(tr, tc)
        setStatus("Checking...", ATRIUM_DIM_COLOR_RES)

        val client = gameApiClient ?: run {
            setStatus("Couldn't reach the engine.", ATRIUM_AMBER_COLOR_RES)
            board.setFEN(fen)
            board.isInteractive = true
            return
        }

        lifecycleScope.launch {
            when (val verify = client.verifyReplayMove(fen, moveUci)) {
                is ApiResult.Success -> {
                    if (verify.data.isCorrect) {
                        creditXpAndDismiss(client)
                    } else {
                        setStatus("Not quite, try again.", ATRIUM_AMBER_COLOR_RES)
                        board.setFEN(fen)
                        board.isInteractive = true
                    }
                }
                is ApiResult.HttpError -> {
                    setStatus(
                        if (verify.code == 503) "Engine busy. Try again."
                        else "Move couldn't be verified.",
                        ATRIUM_AMBER_COLOR_RES,
                    )
                    board.setFEN(fen)
                    board.isInteractive = true
                }
                is ApiResult.NetworkError, ApiResult.Timeout -> {
                    setStatus("Offline. Try again later.", ATRIUM_AMBER_COLOR_RES)
                    board.setFEN(fen)
                    board.isInteractive = true
                }
            }
        }
    }

    private suspend fun creditXpAndDismiss(client: GameApiClient) {
        when (val solve = client.submitTrainingSolve(
            sourceType = MistakeReplayBottomSheet.SOURCE_TYPE_MISTAKE_REPLAY,
            sourceRef = sourceRef.ifBlank { null },
        )) {
            is ApiResult.Success -> {
                val awarded = solve.data.xpAwarded
                val total = solve.data.trainingXp
                requireContext()
                    .getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                    .edit()
                    .putInt(MainActivity.PREF_TRAINING_XP, total)
                    .apply()

                // Advance the study plan: mark this day's puzzle solved
                // so the week progresses (day 0 -> 3 -> 7) and completes.
                // Best-effort — XP is already credited above; if this call
                // fails the day simply resurfaces on the next
                // /coach/plan/today fetch (the endpoint is idempotent), so
                // we don't block the success UX on it.
                if (planId.isNotBlank()) {
                    client.completePlanPuzzle(planId = planId, dayOffset = dayOffset)
                }

                val toastText =
                    if (awarded > 0) "+$awarded XP"
                    else "Already solved · $total XP"
                Toast.makeText(requireContext(), toastText, Toast.LENGTH_SHORT).show()
                dismiss()
            }
            else -> {
                // Verify succeeded but the solve persist failed —
                // leave the sheet open so the user doesn't lose the
                // "I solved it" moment.
                setStatus("Solved, but couldn't save. Try again.", ATRIUM_AMBER_COLOR_RES)
                board.setFEN(fen)
                board.isInteractive = true
            }
        }
    }

    private fun setStatus(text: String, colorRes: Int) {
        statusView.text = text
        statusView.setTextColor(ContextCompat.getColor(requireContext(), colorRes))
    }

    companion object {
        private const val ARG_PLAN_ID = "plan_id"
        private const val ARG_DAY_OFFSET = "day_offset"
        private const val ARG_TOTAL_DAYS = "total_days"
        private const val ARG_THEME = "theme"
        private const val ARG_VERDICT = "verdict"
        private const val ARG_FEN = "fen"
        private const val ARG_EXPECTED_MOVE_UCI = "expected_move_uci"

        private val ATRIUM_DIM_COLOR_RES = R.color.atrium_dim
        private val ATRIUM_AMBER_COLOR_RES = R.color.atrium_accent_amber

        @Suppress("LongParameterList")
        fun newInstance(
            planId: String,
            dayOffset: Int,
            totalDays: Int,
            theme: String,
            verdict: String,
            fen: String,
            expectedMoveUci: String,
        ): TodaysDrillBottomSheet = TodaysDrillBottomSheet().apply {
            arguments = Bundle().apply {
                putString(ARG_PLAN_ID, planId)
                putInt(ARG_DAY_OFFSET, dayOffset)
                putInt(ARG_TOTAL_DAYS, totalDays)
                putString(ARG_THEME, theme)
                putString(ARG_VERDICT, verdict)
                putString(ARG_FEN, fen)
                putString(ARG_EXPECTED_MOVE_UCI, expectedMoveUci)
            }
        }

        /**
         * Compose the ``source_ref`` value that flows into
         * /training/solve on a verified-correct attempt.  Shape:
         * ``plan_<plan_id>:day_<day_offset>``.  The
         * ``(player, source_type, source_ref)`` dedup triple on the
         * solve table means each day-N puzzle is credit-once across
         * retries — exactly the semantics the existing mistake-replay
         * surface uses (with a different prefix).
         *
         * Pure helper so unit tests pin the exact format the server
         * sees without standing up the fragment.
         */
        fun formatSourceRef(planId: String, dayOffset: Int): String =
            "plan_${planId}:day_${dayOffset}"

        /**
         * "Day N of 3 · <theme>" kicker rendered above the title.
         * Day 0 displays as "Day 1" because the user thinks in
         * 1-indexed terms; the ``dayOffset`` field on the wire is
         * just the spaced-repetition step (0 / 3 / 7).
         *
         * For ``theme="generic"`` (or empty), the kicker collapses to
         * "Day N of M" without the trailing themed segment.
         */
        fun formatKicker(dayOffset: Int, totalDays: Int, theme: String): String {
            val displayDay = when (dayOffset) {
                0 -> 1
                3 -> 2
                7 -> 3
                else -> 1
            }
            val themeLabel = prettyTheme(theme)
            return if (themeLabel.isEmpty()) {
                "Day $displayDay of $totalDays"
            } else {
                "Day $displayDay of $totalDays · $themeLabel"
            }
        }

        /**
         * Map a server-side theme tag (snake_case) to a sentence-case
         * label, returning the empty string when the theme is
         * ``"generic"`` (treated as "no specific theme to surface").
         */
        fun prettyTheme(theme: String): String {
            val tag = theme.trim().lowercase()
            if (tag.isEmpty() || tag == "generic") return ""
            val parts = tag.split('_')
            return parts.first().replaceFirstChar(Char::uppercaseChar) +
                parts.drop(1).joinToString("") { " $it" }
        }
    }
}
