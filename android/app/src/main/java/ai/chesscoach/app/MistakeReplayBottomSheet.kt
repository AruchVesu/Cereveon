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
 * Cereveon · Atrium · Mistake-replay bottom sheet (Phase 3).
 *
 * Launched from [GameSummaryBottomSheet] when the /game/finish response
 * carries a non-null ``biggest_mistake`` block.  Shows the position
 * the user was looking at when they made their worst centipawn-loss
 * move + the move they actually played, and lets them try a stronger
 * alternative on an interactive [ChessBoardView].
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
 *      with ``source_type=mistake_replay`` and the ``source_ref``
 *      that came down the wire on the original ``biggest_mistake``.
 *      Server credits +10 XP; activity toasts "+10 XP", updates
 *      ``PREF_TRAINING_XP`` so the Home kicker bumps on next paint,
 *      and dismisses.
 *  4b. ``isCorrect=false`` → status flips to "Not quite, try again"
 *      (amber), the board's FEN is reset to the mistake position,
 *      and ``isInteractive`` is re-enabled.  No XP penalty; the
 *      user can retry indefinitely.
 *
 * Trust boundary
 * --------------
 * The server's verify endpoint is the trust anchor — a modded client
 * could in principle skip /training/verify-replay and post directly
 * to /training/solve, but the dedup constraint
 * ``(player, source_type, source_ref)`` means each mistake can be
 * credited at most once anyway.  Phase 4+ can tighten by requiring
 * a server-issued nonce that proves the client went through
 * /training/verify-replay.
 *
 * Args
 * ----
 * Carried as bundle extras.  See [newInstance] for the canonical
 * construction path used by [GameSummaryBottomSheet].
 */
class MistakeReplayBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host activity before [show]; required for the verify + solve calls. */
    var gameApiClient: GameApiClient? = null

    private lateinit var board: ChessBoardView
    private lateinit var statusView: TextView

    private var fen: String = ""
    private var sourceRef: String = ""

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_mistake_replay, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val args = requireArguments()
        fen = args.getString(ARG_FEN, "")
        val playedMove = args.getString(ARG_PLAYED_MOVE, "")
        val moveNumber = args.getInt(ARG_MOVE_NUMBER, 0)
        val evalLossCp = args.getInt(ARG_EVAL_LOSS_CP, 0)
        sourceRef = args.getString(ARG_SOURCE_REF, "")

        view.findViewById<TextView>(R.id.mistakeReplayKicker).text =
            formatKicker(moveNumber)
        view.findViewById<TextView>(R.id.mistakeReplayPlayedMove).text =
            formatPlayedMoveLine(playedMove, evalLossCp)

        board = view.findViewById(R.id.mistakeReplayBoard)
        statusView = view.findViewById(R.id.mistakeReplayStatus)

        // Seed the board to the mistake position.  ``setFEN`` resets
        // last-move highlights and the selection state so the sheet
        // opens to a clean "your turn" presentation.
        board.setFEN(fen)
        board.isInteractive = true

        board.onMovePlayed = { fr, fc, tr, tc ->
            handleAttempt(fr, fc, tr, tc)
        }

        view.findViewById<Button>(R.id.mistakeReplayCloseButton).setOnClickListener {
            dismiss()
        }
    }

    private fun handleAttempt(fr: Int, fc: Int, tr: Int, tc: Int) {
        // Lock the board the moment the move lands so the user can't
        // fire a second attempt while the first is in flight.  The
        // wrong-move recovery path resets ``isInteractive=true``;
        // the correct-move path dismisses the sheet.
        board.isInteractive = false

        // Apply the move visually so the user sees their attempt
        // play out on the board before the verify round-trip lands.
        // If the move is illegal the board's own legality check
        // refuses it (no-op), so we never round-trip an impossible
        // UCI string to the server.
        val moveResult = board.applyMove(fr, fc, tr, tc)
        if (moveResult == MoveResult.FAILED) {
            // Illegal in this position (e.g. moves into check).
            // Reset interactivity; status stays at the default.
            board.isInteractive = true
            return
        }

        val moveUci = rowColToUci(fr, fc) + rowColToUci(tr, tc)
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
            sourceType = SOURCE_TYPE_MISTAKE_REPLAY,
            sourceRef = sourceRef.ifBlank { null },
        )) {
            is ApiResult.Success -> {
                val awarded = solve.data.xpAwarded
                val total = solve.data.trainingXp
                // Update the cache so the Home Level/XP kicker
                // re-renders to the new total on next paint without
                // waiting for the next /auth/me cold-start.
                requireContext()
                    .getSharedPreferences(
                        MainActivity.PREFS_NAME,
                        Context.MODE_PRIVATE,
                    )
                    .edit()
                    .putInt(MainActivity.PREF_TRAINING_XP, total)
                    .apply()

                val toastText =
                    if (awarded > 0) "+$awarded XP"
                    else "Already solved · $total XP"
                Toast.makeText(requireContext(), toastText, Toast.LENGTH_SHORT).show()
                dismiss()
            }
            else -> {
                // Verify succeeded but the solve persist failed —
                // the user nailed the move but we couldn't bank the
                // XP.  Show a soft message + leave the sheet open
                // so they don't lose the "I solved it" moment.
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
        private const val ARG_FEN = "fen"
        private const val ARG_PLAYED_MOVE = "played_move"
        private const val ARG_MOVE_NUMBER = "move_number"
        private const val ARG_EVAL_LOSS_CP = "eval_loss_cp"
        private const val ARG_SOURCE_REF = "source_ref"

        /** Source-type string accepted by POST /training/solve for mistake-replay solves. */
        const val SOURCE_TYPE_MISTAKE_REPLAY: String = "mistake_replay"

        // Status colour tokens — referenced via resource ids so a
        // theme change picks them up without touching this file.
        private val ATRIUM_DIM_COLOR_RES = R.color.atrium_dim
        private val ATRIUM_AMBER_COLOR_RES = R.color.atrium_accent_amber

        fun newInstance(mistake: BiggestMistakeDto): MistakeReplayBottomSheet =
            MistakeReplayBottomSheet().apply {
                arguments = Bundle().apply {
                    putString(ARG_FEN, mistake.fen)
                    putString(ARG_PLAYED_MOVE, mistake.playedMove)
                    putInt(ARG_MOVE_NUMBER, mistake.moveNumber)
                    putInt(ARG_EVAL_LOSS_CP, mistake.evalLossCp)
                    putString(ARG_SOURCE_REF, mistake.sourceRef)
                }
            }

        /**
         * Convert ChessBoardView's (row, col) coords (row 0 = rank 8,
         * col 0 = file a) to a 2-char UCI square like ``e2``.  Pure
         * helper so unit tests can pin the conversion without
         * standing up a view.
         */
        fun rowColToUci(row: Int, col: Int): String {
            val file = ('a' + col)
            val rank = (8 - row).toString()
            return "$file$rank"
        }

        /** "Mistake · Move 14" kicker copy. */
        fun formatKicker(moveNumber: Int): String =
            "Mistake · Move $moveNumber"

        /**
         * "You played e2e4 — eval dropped by 240 cp." subline.  Pure
         * helper so the wording is testable and the activity-only
         * findViewById noise stays out of the unit test surface.
         */
        fun formatPlayedMoveLine(playedMoveUci: String, evalLossCp: Int): String =
            "You played $playedMoveUci — eval dropped by $evalLossCp cp."
    }
}
