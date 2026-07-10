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
 * Cereveon · Atrium · Puzzle trainer bottom sheet (Puzzles tab).
 *
 * Endless practice-puzzle stream, decoupled from the per-mistake study
 * plan.  Each puzzle comes from ``GET /puzzles/next`` — live-fetched
 * from Lichess at the player's rating-derived difficulty, with a
 * server-side fallback to the curated corpus (one wire shape either
 * way; [PuzzleNextDto.source] carries the attribution).
 *
 * Flow per puzzle
 * ---------------
 *  1.  [fetchNextPuzzle] loads a position; the board flips when Black
 *      is to move so the solver's colour sits at the bottom, and the
 *      status row announces the side ("White to move").
 *  2.  A move attempt round-trips ``POST /training/verify-replay`` —
 *      the LOCAL engine judges it; the Lichess solution move is never
 *      the oracle (same trust anchor as the drill sheets).
 *  3a. Correct → ``POST /training/solve`` with
 *      ``source_type="standard_puzzle"`` and
 *      ``source_ref=<puzzle_id>`` credits +10 XP (deduped per puzzle
 *      by the server's unique triple), the XP cache refreshes so Home
 *      re-renders, and the board locks on the solved position until
 *      "Next puzzle" advances.
 *  3b. Wrong → "Not quite, try again." — position resets, no penalty,
 *      unlimited retries (matches [TodaysDrillBottomSheet]).
 *
 * "Next puzzle" doubles as a skip: tapping it before a solve just
 * fetches a fresh position (no XP, no penalty).
 */
class PuzzleTrainerBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host before [show]; required for every call. */
    var gameApiClient: GameApiClient? = null

    private lateinit var board: ChessBoardView
    private lateinit var kickerView: TextView
    private lateinit var statusView: TextView
    private lateinit var nextButton: Button

    private var puzzle: PuzzleNextDto? = null

    /** True while a /puzzles/next fetch is in flight — debounces the
     *  Next button so a double-tap can't burn two fetches. */
    private var fetching: Boolean = false

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_puzzle_trainer, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        kickerView = view.findViewById(R.id.puzzleTrainerKicker)
        statusView = view.findViewById(R.id.puzzleTrainerStatus)
        board = view.findViewById(R.id.puzzleTrainerBoard)
        nextButton = view.findViewById(R.id.puzzleTrainerNextButton)

        board.isInteractive = false
        board.onMovePlayed = { fr, fc, tr, tc -> handleAttempt(fr, fc, tr, tc) }

        nextButton.setOnClickListener { fetchNextPuzzle() }
        view.findViewById<Button>(R.id.puzzleTrainerCloseButton).setOnClickListener {
            dismiss()
        }

        fetchNextPuzzle()
    }

    /**
     * Load the next puzzle.  On failure the status row carries the
     * error and the Next button stays live as the retry affordance —
     * the sheet never dead-ends silently.
     */
    private fun fetchNextPuzzle() {
        if (fetching) return
        val client = gameApiClient ?: run {
            setStatus("Couldn't load a puzzle. Try again.", ATRIUM_AMBER_COLOR_RES)
            return
        }
        fetching = true
        board.isInteractive = false
        setStatus("Finding a puzzle...", ATRIUM_DIM_COLOR_RES)

        lifecycleScope.launch {
            val result = try {
                client.getNextPuzzle()
            } catch (_: Exception) {
                null
            } finally {
                fetching = false
            }
            val next = (result as? ApiResult.Success)?.data
            if (next == null || next.fen.isBlank()) {
                setStatus("Couldn't load a puzzle. Try again.", ATRIUM_AMBER_COLOR_RES)
                return@launch
            }
            puzzle = next
            kickerView.text = formatKicker(next)
            board.setFEN(next.fen)
            // Solver's colour at the bottom — a random puzzle can put
            // the user on either side.
            board.flipped = isBlackToMove(next.fen)
            board.isInteractive = true
            setStatus(sideToMoveLabel(next.fen), ATRIUM_DIM_COLOR_RES)
        }
    }

    private fun handleAttempt(fr: Int, fc: Int, tr: Int, tc: Int) {
        val current = puzzle ?: return
        // Lock the board the moment the move lands so the user can't
        // fire a second attempt while the first is in flight.  Wrong-
        // move recovery re-enables; a solve leaves it locked until
        // "Next puzzle" advances.
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
            resetPosition(current)
            return
        }

        lifecycleScope.launch {
            when (val verify = client.verifyReplayMove(current.fen, moveUci)) {
                is ApiResult.Success -> {
                    if (verify.data.isCorrect) {
                        creditXp(client, current)
                    } else {
                        setStatus("Not quite, try again.", ATRIUM_AMBER_COLOR_RES)
                        resetPosition(current)
                    }
                }
                is ApiResult.HttpError -> {
                    setStatus(
                        if (verify.code == 503) "Engine busy. Try again."
                        else "Move couldn't be verified.",
                        ATRIUM_AMBER_COLOR_RES,
                    )
                    resetPosition(current)
                }
                is ApiResult.NetworkError, ApiResult.Timeout -> {
                    setStatus("Offline. Try again later.", ATRIUM_AMBER_COLOR_RES)
                    resetPosition(current)
                }
            }
        }
    }

    private suspend fun creditXp(client: GameApiClient, current: PuzzleNextDto) {
        when (val solve = client.submitTrainingSolve(
            sourceType = SOURCE_TYPE_STANDARD_PUZZLE,
            sourceRef = current.puzzleId.ifBlank { null },
        )) {
            is ApiResult.Success -> {
                val awarded = solve.data.xpAwarded
                val total = solve.data.trainingXp
                requireContext()
                    .getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                    .edit()
                    .putInt(MainActivity.PREF_TRAINING_XP, total)
                    .apply()

                val toastText =
                    if (awarded > 0) "+$awarded XP"
                    else "Already solved · $total XP"
                Toast.makeText(requireContext(), toastText, Toast.LENGTH_SHORT).show()
                // Board stays locked on the solved position — "Next
                // puzzle" is the explicit advance, so the user keeps
                // the solved moment instead of the board yanking away.
                setStatus(
                    if (awarded > 0) "Solved · +$awarded XP" else "Solved",
                    ATRIUM_CYAN_COLOR_RES,
                )
            }
            else -> {
                // Verify succeeded but the solve persist failed —
                // leave the puzzle live so the user doesn't lose the
                // "I solved it" moment; replaying the move retries.
                setStatus("Solved, but couldn't save. Try again.", ATRIUM_AMBER_COLOR_RES)
                resetPosition(current)
            }
        }
    }

    /** Reset the board to the puzzle position and re-enable input. */
    private fun resetPosition(current: PuzzleNextDto) {
        board.setFEN(current.fen)
        board.isInteractive = true
    }

    private fun setStatus(text: String, colorRes: Int) {
        statusView.text = text
        statusView.setTextColor(ContextCompat.getColor(requireContext(), colorRes))
    }

    companion object {
        /** ``TrainingCompletion.source_type`` for standalone puzzles —
         *  mirrors ``llm.seca.training.models.SOURCE_TYPE_STANDARD_PUZZLE``. */
        const val SOURCE_TYPE_STANDARD_PUZZLE = "standard_puzzle"

        private val ATRIUM_DIM_COLOR_RES = R.color.atrium_dim
        private val ATRIUM_AMBER_COLOR_RES = R.color.atrium_accent_amber
        private val ATRIUM_CYAN_COLOR_RES = R.color.atrium_accent_cyan

        /**
         * "Puzzle · <theme> · <difficulty> · via Lichess" kicker, with
         * empty segments dropped:
         *
         *  * theme — corpus tag rendered via
         *    [TodaysDrillBottomSheet.prettyTheme]; the Lichess feed's
         *    ``"mix"`` (and ``"generic"``) collapse to nothing.
         *  * difficulty — capitalised band, omitted when the server
         *    sent none.
         *  * "via Lichess" — only for live-fetched puzzles; corpus
         *    picks carry no attribution.
         *
         * Pure function — unit-testable without a fragment.
         */
        fun formatKicker(puzzle: PuzzleNextDto): String {
            val parts = mutableListOf("Puzzle")
            val theme = puzzle.theme.trim().lowercase()
            if (theme != "mix") {
                val pretty = TodaysDrillBottomSheet.prettyTheme(theme)
                if (pretty.isNotEmpty()) parts.add(pretty)
            }
            val difficulty = puzzle.difficulty.trim().lowercase()
            if (difficulty.isNotEmpty()) {
                parts.add(difficulty.replaceFirstChar(Char::uppercaseChar))
            }
            if (puzzle.source.trim().lowercase() == "lichess") {
                parts.add("via Lichess")
            }
            return parts.joinToString(" · ")
        }

        /**
         * True when the FEN's side-to-move field is Black — drives the
         * board flip so the solver's pieces sit at the bottom.  A
         * malformed FEN defaults to White (unflipped), matching the
         * board's own lenient parsing.  Pure function.
         */
        fun isBlackToMove(fen: String): Boolean =
            fen.trim().split(" ").getOrNull(1)?.lowercase() == "b"

        /** "White to move" / "Black to move" status line.  Pure function. */
        fun sideToMoveLabel(fen: String): String =
            if (isBlackToMove(fen)) "Black to move" else "White to move"
    }
}
