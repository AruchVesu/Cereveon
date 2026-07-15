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
 * Single-move vs multi-move drills
 * --------------------------------
 * ``today_puzzle.solution_line_uci`` (empty for day-0 originals and for
 * legacy servers) carries the full solution walk of a library puzzle:
 * SOLVER moves at even indices, opponent replies at odd ones.  With a
 * walkable line (>= 3 plies, i.e. at least two solver decisions) the
 * sheet drills the WHOLE combination: after each verified-correct solver
 * move that follows the line, the opponent's scripted reply auto-plays
 * and the user must find the next move.  Without one, behaviour is the
 * original single-decision drill.
 *
 * Flow per attempt
 * ----------------
 *  1.  User taps & drags a piece on [ChessBoardView] → fires
 *      ``onMovePlayed(fr, fc, tr, tc)``.
 *  2.  Board is locked (``isInteractive=false``) and the status text
 *      flips to "Checking...".
 *  3.  Activity calls ``POST /training/verify-replay`` with the FEN of
 *      the CURRENT decision point (the puzzle start, or mid-line after
 *      auto-played replies) and the UCI of the attempted move.  Server
 *      runs Stockfish — the engine stays the trust anchor for every
 *      step; the Lichess/corpus line is a walk-through hint only.
 *  4a. ``isCorrect=true`` and the move matches the line with more solver
 *      moves to come → the scripted opponent reply auto-plays, the
 *      status ticks the progress ("Correct — find the next move"), and
 *      the board unlocks at the new decision point.
 *  4b. ``isCorrect=true`` on the line's LAST solver move, on a
 *      single-decision drill, or on an engine-approved DEVIATION from
 *      the line (the engine says the move is sound — we don't punish a
 *      second good solution) → activity calls ``POST /training/solve``
 *      with ``source_type=mistake_replay`` and
 *      ``source_ref=plan_<plan_id>:day_<day_offset>``.  Server credits
 *      +10 XP; activity toasts "+10 XP", updates ``PREF_TRAINING_XP`` so
 *      Home re-renders, and dismisses.
 *  4c. ``isCorrect=false`` → status flips to "Not quite, try again"
 *      (amber), the board resets to the CURRENT decision point (not the
 *      puzzle start — mid-line progress is kept), and ``isInteractive``
 *      is re-enabled.  No XP penalty; the user can retry indefinitely
 *      (matches the [MistakeReplayBottomSheet] UX).
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
 * construction path used by [HomeActivity] / [StudyPlanOverviewBottomSheet].
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

    // Multi-move walk state.  ``solutionLine`` is the full line ([] =
    // single-decision drill); ``lineIndex`` points at the solver move the
    // user must find next; ``currentFen`` is the decision point the board
    // is showing (used for verify calls and wrong-move resets so mid-line
    // progress survives a retry).
    private var solutionLine: List<String> = emptyList()
    private var lineIndex: Int = 0
    private var currentFen: String = ""

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
        solutionLine = args.getStringArrayList(ARG_SOLUTION_LINE)?.toList() ?: emptyList()
        lineIndex = 0
        currentFen = fen
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

        board.loadPosition(fen)
        board.isInteractive = true
        board.onMovePlayed = { fr, fc, tr, tc -> handleAttempt(fr, fc, tr, tc) }

        // Tell the user upfront when the drill runs deeper than one move.
        if (isWalkable(solutionLine)) {
            setStatus(
                formatWalkStatus(found = 0, total = solverMoveCount(solutionLine)),
                ATRIUM_DIM_COLOR_RES,
            )
        }

        view.findViewById<Button>(R.id.todaysDrillCloseButton).setOnClickListener {
            dismiss()
        }
    }

    private fun handleAttempt(fr: Int, fc: Int, tr: Int, tc: Int) {
        // Lock the board the moment the move lands so the user can't
        // fire a second attempt while the first is in flight.  Wrong-
        // move recovery re-enables ``isInteractive``; correct-move
        // path continues the walk or dismisses the sheet.
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
            board.loadPosition(currentFen)
            board.isInteractive = true
            return
        }

        lifecycleScope.launch {
            // Verify against the CURRENT decision point — mid-line, that's
            // the position after the auto-played opponent replies, not the
            // puzzle-start FEN.
            when (val verify = client.verifyReplayMove(currentFen, moveUci)) {
                is ApiResult.Success -> {
                    if (verify.data.isCorrect) {
                        when (val step = nextDrillStep(solutionLine, lineIndex, moveUci)) {
                            is DrillStepOutcome.Continue -> advanceWalk(step)
                            DrillStepOutcome.Solved -> creditXpAndDismiss(client)
                        }
                    } else {
                        setStatus("Not quite, try again.", ATRIUM_AMBER_COLOR_RES)
                        board.loadPosition(currentFen)
                        board.isInteractive = true
                    }
                }
                is ApiResult.HttpError -> {
                    setStatus(
                        if (verify.code == 503) "Engine busy. Try again."
                        else "Move couldn't be verified.",
                        ATRIUM_AMBER_COLOR_RES,
                    )
                    board.loadPosition(currentFen)
                    board.isInteractive = true
                }
                is ApiResult.NetworkError, ApiResult.Timeout -> {
                    setStatus("Offline. Try again later.", ATRIUM_AMBER_COLOR_RES)
                    board.loadPosition(currentFen)
                    board.isInteractive = true
                }
            }
        }
    }

    /**
     * Mid-line continuation: the user's (already applied) move followed the
     * line and more solver moves remain.  Auto-play the scripted opponent
     * reply, advance the walk state, and unlock the board at the new
     * decision point.  A reply the board rejects (which would mean the
     * validated line disagrees with the board's rules engine) falls back
     * to "solved" rather than stranding the user — the engine already
     * approved their move.
     */
    private fun advanceWalk(step: DrillStepOutcome.Continue) {
        // ``applyAIMove`` returns the captured piece ('.' both for a
        // rejected move AND for a legal quiet move), so detect rejection
        // by whether the position changed — an applied move always flips
        // the side to move, so the FEN cannot stay identical.
        val coords = uciToCoords(step.opponentReplyUci)
        val before = board.exportFEN()
        if (coords != null) {
            board.applyAIMove(
                coords[0], coords[1], coords[2], coords[3],
                promo = uciPromotionChar(step.opponentReplyUci),
            )
        }
        if (coords == null || board.exportFEN() == before) {
            // The board refused the scripted reply (or the line is
            // malformed).  The engine already approved the user's move, so
            // finish the drill rather than strand them mid-walk.
            val client = gameApiClient ?: return
            lifecycleScope.launch { creditXpAndDismiss(client) }
            return
        }
        lineIndex = step.nextLineIndex
        currentFen = board.exportFEN()
        setStatus(
            formatWalkStatus(
                found = lineIndex / 2,
                total = solverMoveCount(solutionLine),
            ),
            ATRIUM_CYAN_COLOR_RES,
        )
        board.isInteractive = true
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
                board.loadPosition(currentFen)
                board.isInteractive = true
            }
        }
    }

    private fun setStatus(text: String, colorRes: Int) {
        statusView.text = text
        statusView.setTextColor(ContextCompat.getColor(requireContext(), colorRes))
    }

    /**
     * What happens after an ENGINE-APPROVED user move, given the walk
     * state.  Pure — the fragment supplies the board/network side effects.
     */
    sealed class DrillStepOutcome {
        /** More solver moves remain: auto-play [opponentReplyUci], then the
         *  user must find the solver move at [nextLineIndex]. */
        data class Continue(val opponentReplyUci: String, val nextLineIndex: Int) :
            DrillStepOutcome()

        /** The drill is complete — credit XP and dismiss. */
        object Solved : DrillStepOutcome()
    }

    companion object {
        private const val ARG_PLAN_ID = "plan_id"
        private const val ARG_DAY_OFFSET = "day_offset"
        private const val ARG_TOTAL_DAYS = "total_days"
        private const val ARG_THEME = "theme"
        private const val ARG_VERDICT = "verdict"
        private const val ARG_FEN = "fen"
        private const val ARG_EXPECTED_MOVE_UCI = "expected_move_uci"
        private const val ARG_SOLUTION_LINE = "solution_line_uci"

        private val ATRIUM_DIM_COLOR_RES = R.color.atrium_dim
        private val ATRIUM_AMBER_COLOR_RES = R.color.atrium_accent_amber
        private val ATRIUM_CYAN_COLOR_RES = R.color.atrium_accent_cyan

        @Suppress("LongParameterList")
        fun newInstance(
            planId: String,
            dayOffset: Int,
            totalDays: Int,
            theme: String,
            verdict: String,
            fen: String,
            expectedMoveUci: String,
            solutionLineUci: List<String> = emptyList(),
        ): TodaysDrillBottomSheet = TodaysDrillBottomSheet().apply {
            arguments = Bundle().apply {
                putString(ARG_PLAN_ID, planId)
                putInt(ARG_DAY_OFFSET, dayOffset)
                putInt(ARG_TOTAL_DAYS, totalDays)
                putString(ARG_THEME, theme)
                putString(ARG_VERDICT, verdict)
                putString(ARG_FEN, fen)
                putString(ARG_EXPECTED_MOVE_UCI, expectedMoveUci)
                putStringArrayList(ARG_SOLUTION_LINE, ArrayList(solutionLineUci))
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

        // ── Multi-move walk helpers (pure, unit-tested) ─────────────────

        /**
         * A line is worth walking when it carries at least two solver
         * decisions (solver, opponent, solver = 3 plies).  Shorter lines
         * (including the empty one from day-0 originals and legacy
         * servers) run the original single-decision drill.
         */
        fun isWalkable(line: List<String>): Boolean = line.size >= 3

        /** Number of solver decisions in a line: plies at even indices. */
        fun solverMoveCount(line: List<String>): Int = (line.size + 1) / 2

        /**
         * Decide what follows an ENGINE-APPROVED user move at
         * ``line[lineIndex]``'s decision point:
         *
         * * The move matches the line and a further solver move exists →
         *   [DrillStepOutcome.Continue] carrying the opponent's scripted
         *   reply (``line[lineIndex + 1]``) and the next solver index.
         * * Anything else — the line's last solver move, a single-decision
         *   drill, or an engine-approved DEVIATION from the line (position
         *   diverged; the rest of the script no longer applies, and the
         *   engine already vouched for the move) → [DrillStepOutcome.Solved].
         *
         * Only ever called after the engine verified the move; wrong moves
         * never reach this decision.
         */
        fun nextDrillStep(
            line: List<String>,
            lineIndex: Int,
            userMoveUci: String,
        ): DrillStepOutcome {
            val followsLine = isWalkable(line) &&
                lineIndex >= 0 &&
                lineIndex < line.size &&
                line[lineIndex] == userMoveUci
            val hasNextSolverMove = followsLine && lineIndex + 2 <= line.size - 1
            return if (hasNextSolverMove) {
                DrillStepOutcome.Continue(
                    opponentReplyUci = line[lineIndex + 1],
                    nextLineIndex = lineIndex + 2,
                )
            } else {
                DrillStepOutcome.Solved
            }
        }

        /**
         * Board coordinates for a UCI move: ``[fromRow, fromCol, toRow,
         * toCol]`` in [ChessBoardView]'s convention (row 0 = rank 8,
         * col 0 = file a), or ``null`` for a malformed string.  Inverse of
         * [MistakeReplayBottomSheet.rowColToUci].
         */
        fun uciToCoords(uci: String): IntArray? {
            if (uci.length < 4) return null
            val fc = uci[0] - 'a'
            val fr = 8 - (uci[1] - '0')
            val tc = uci[2] - 'a'
            val tr = 8 - (uci[3] - '0')
            val valid = listOf(fr, fc, tr, tc).all { it in 0..7 }
            return if (valid) intArrayOf(fr, fc, tr, tc) else null
        }

        /**
         * Promotion piece letter of a 5-char UCI move (``e7e8q`` → ``q``),
         * or ``' '`` when the move carries none — the shape
         * [ChessBoardView.applyAIMove] expects for its ``promo`` argument.
         */
        fun uciPromotionChar(uci: String): Char =
            if (uci.length >= 5) uci[4] else ' '

        /**
         * Walk progress line: how many solver moves are found out of the
         * line's total.  ``found = 0`` announces the depth upfront;
         * intermediate steps celebrate and point forward.  Pure —
         * unit-testable without a view.
         */
        fun formatWalkStatus(found: Int, total: Int): String = when {
            found <= 0 -> "This one runs deeper — find $total moves."
            else -> "Correct — find the next move ($found of $total)."
        }
    }
}
