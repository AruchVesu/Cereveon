package ai.chesscoach.app

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class Turn { HUMAN, AI }

class ChessViewModel(
    private val engineProvider: EngineProvider = NativeEngineProvider(),
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.Default,
    /** Injected after construction; null disables real engine eval (falls back to "?" score). */
    var engineEvalClient: EngineEvalClient? = null,
    /** Injected after construction; null disables live per-move coaching hints. */
    var liveCoachClient: LiveMoveClient? = null,
    /**
     * Optional source of the server-derived `opponentElo` for the AI
     * strength dial.  Mutable (rather than `private val`) so the
     * activity can wire it in after the ChessViewModel has been
     * resolved by `by viewModels()` — same pattern as
     * [engineEvalClient] / [liveCoachClient].  Without this wiring
     * the strength always falls back to the no-cache default of 100,
     * which is what the AdaptiveEngineWiringTest's
     * NO_CACHE_DEFAULTS_TO_100 case pins.
     *
     * Invalidate the cache (via [PlayerProfileCache.invalidate]) after
     * every /game/finish so the next AI move sees the rating bump
     * the server applied.
     */
    var playerProfileCache: PlayerProfileCache? = null,
) : ViewModel() {

    private var turn: Turn = Turn.HUMAN
    private var aiThinking = false

    private var stateId: Long = 0
    private var aiJob: Job? = null

    private var lastHumanMoveHint: String? = null
    private var lastHumanMoveClassification: MistakeClassification? = null
    private var lastHumanMoveEngineSignal: EngineSignalDto? = null

    // ── Move history for PGN export ──────────────────────────────────────────
    private val moveHistory = mutableListOf<String>()

    /**
     * Called on the Main thread after each AI move with a [QuickCoachUpdate].
     * When [engineEvalClient] is set, the update contains the real Stockfish
     * centipawn score; otherwise the score field is "?" (engine unavailable).
     * When [liveCoachClient] is set, [QuickCoachUpdate.explanation] is the
     * per-move coaching hint from POST /live/move.
     */
    var onQuickCoachUpdate: ((QuickCoachUpdate) -> Unit)? = null

    /** Number of half-moves played so far (human + AI combined). */
    val moveCount: Int get() = moveHistory.size

    /**
     * Comma-separated UCI move list — used by [MainActivity]'s Resume
     * snapshot to persist the full move history without paying the
     * cost of JSON serialisation for what is always a small list of
     * 4–5-character tokens.  Empty string when no moves have been
     * played; [restoreMoveHistory] accepts the inverse.
     */
    fun exportUciHistory(): String = moveHistory.joinToString(",")

    /**
     * Returns the game moves as a well-formed PGN string including the four
     * mandatory headers required by the backend [GameFinishRequest] validator.
     *
     * Without headers the backend raises a 422 "invalid PGN: no PGN headers
     * found" error, silently failing every /game/finish call.
     */
    fun exportPGN(): String {
        if (moveHistory.isEmpty()) return "(no moves)"
        val moves = moveHistory
            .mapIndexed { index, uci ->
                if (index % 2 == 0) "${index / 2 + 1}. $uci" else uci
            }
            .joinToString(" ")
        return """[Event "Chess Coach Game"]
[White "Player"]
[Black "Engine"]
[Result "*"]

$moves"""
    }

    private fun uciFromCoords(fr: Int, fc: Int, tr: Int, tc: Int): String {
        val files = "abcdefgh"
        return "${files[fc]}${8 - fr}${files[tc]}${8 - tr}"
    }

    private fun assertTurn(expected: Turn) {
        check(turn == expected) {
            "ILLEGAL MOVE: $expected expected, but was $turn"
        }
    }

    private fun invalidateState() {
        stateId++
        aiJob?.cancel()
        aiThinking = false
        turn = Turn.HUMAN
        moveHistory.clear()
        lastHumanMoveHint = null
        lastHumanMoveClassification = null
        lastHumanMoveEngineSignal = null
        Log.d("STATE", "Game state invalidated. New ID: $stateId")
    }

    fun onHumanMove(
        fr: Int, fc: Int, tr: Int, tc: Int,
        applyHumanMove: () -> MoveResult,
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int) -> Char,
    ) {
        if (turn != Turn.HUMAN) return

        val requestId = stateId

        viewModelScope.launch(ioDispatcher) {
            val result = withContext(Dispatchers.Main) { applyHumanMove() }

            withContext(Dispatchers.Main) {
                if (stateId != requestId) return@withContext

                when (result) {
                    MoveResult.SUCCESS -> {
                        val humanUci = uciFromCoords(fr, fc, tr, tc)
                        moveHistory.add(humanUci)
                        turn = Turn.AI
                        val fenAfterHuman = exportFEN()
                        dispatchHumanMoveCoach(fenAfterHuman, humanUci, requestId)
                        requestAIMove(exportFEN, applyAIMove)
                    }
                    MoveResult.PROMOTION -> {
                        Log.d("TURN", "Human promotion pending...")
                    }
                    MoveResult.FAILED -> {}
                }
            }
        }
    }

    fun onPromotionFinished(
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int) -> Char,
    ) {
        if (turn != Turn.HUMAN) return
        turn = Turn.AI
        requestAIMove(exportFEN, applyAIMove)
    }

    private fun requestAIMove(
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int) -> Char,
    ) {
        if (aiThinking || turn != Turn.AI) return
        aiThinking = true

        val requestId = stateId

        aiJob = viewModelScope.launch(ioDispatcher) {
            try {
                val fen = withContext(Dispatchers.Main) { exportFEN() }

                val strengthLevel: Int = playerProfileCache?.let {
                    try { EloToStrength.map(it.getOpponentElo()) } catch (_: Exception) { 100 }
                } ?: 100

                val move = engineProvider.getBestMove(fen, strengthLevel)

                withContext(Dispatchers.Main) {
                    if (stateId == requestId) {
                        val captured = processAIMoveResult(move, applyAIMove)
                        if (captured != null) {
                            // uci is only valid after isValid() passes — compute here
                            val uci = move?.let { uciFromCoords(it.fr, it.fc, it.tr, it.tc) } ?: ""
                            dispatchEngineEval(captured, uci, exportFEN, requestId)
                        }
                    } else {
                        Log.w("AI_TEST", "Discarding AI move from stale state ($requestId vs $stateId)")
                    }
                }
            } finally {
                aiThinking = false
            }
        }
    }

    /**
     * Fires immediately after the human's move: calls POST /live/move with the
     * human's FEN and UCI, stores the result, and emits a [QuickCoachUpdate]
     * with [QuickCoachUpdate.isHumanMoveCoachUpdate] = true.
     *
     * No-ops when [liveCoachClient] is null.
     */
    private fun dispatchHumanMoveCoach(
        fen: String,
        uci: String,
        requestId: Long,
    ) {
        val liveClient = liveCoachClient ?: return
        viewModelScope.launch(ioDispatcher) {
            val liveResult = if (uci.length in 4..5) liveClient.getLiveCoaching(fen, uci) else null
            withContext(Dispatchers.Main) {
                if (stateId != requestId) return@withContext
                val liveSuccess = liveResult as? ApiResult.Success
                val liveHint = liveSuccess?.data?.hint?.takeIf { it.isNotBlank() }
                val backendClassification = liveSuccess?.data?.moveQuality
                    ?.takeIf { it.isNotBlank() }
                    ?.let { QuickCoachLogic.fromBackendString(it) }
                val liveEngineSignal = liveSuccess?.data?.engineSignal
                lastHumanMoveHint = liveHint
                lastHumanMoveClassification = backendClassification
                lastHumanMoveEngineSignal = liveEngineSignal
                val update = QuickCoachLogic.buildUpdateFromEngine(
                    capturedPiece = '.',
                    engineScore = null,
                    liveHint = liveHint,
                    engineAvailable = true,
                    classificationOverride = backendClassification,
                    engineSignal = liveEngineSignal,
                    isHumanMoveCoachUpdate = true,
                )
                onQuickCoachUpdate?.invoke(update)
            }
        }
    }

    /**
     * Obtains the Stockfish centipawn evaluation after the AI move and emits a
     * [QuickCoachUpdate] via [onQuickCoachUpdate].
     *
     * The coaching hint displayed is sourced from [lastHumanMoveHint], which was
     * stored by [dispatchHumanMoveCoach] earlier in the same turn (Mode-1 fires
     * after the human's move, not the AI's move).
     *
     * Falls back to a "?" score when [engineEvalClient] is null.
     * [QuickCoachUpdate.engineAvailable] is set to false on eval errors.
     *
     * Must be called on the Main thread immediately after [processAIMoveResult].
     *
     * @param capturedPiece Piece char that the AI captured ('.' if none).
     * @param uci           The AI move in UCI notation (unused — kept for signature compat).
     * @param exportFEN     Lambda that exports the current board FEN (post-AI).
     * @param requestId     State snapshot to guard against stale results after reset.
     */
    private fun dispatchEngineEval(
        capturedPiece: Char,
        uci: String,
        exportFEN: () -> String,
        requestId: Long,
    ) {
        val evalClient = engineEvalClient

        if (evalClient == null) {
            onQuickCoachUpdate?.invoke(
                QuickCoachLogic.buildUpdateFromEngine(
                    capturedPiece,
                    null,
                    liveHint = lastHumanMoveHint,
                    classificationOverride = lastHumanMoveClassification,
                    engineSignal = lastHumanMoveEngineSignal,
                )
            )
            return
        }

        val fenAfterAI = exportFEN()
        viewModelScope.launch(ioDispatcher) {
            val evalResult = evalClient.evaluate(fenAfterAI)

            withContext(Dispatchers.Main) {
                if (stateId == requestId) {
                    val evalSuccess = evalResult as? ApiResult.Success
                    val score = evalSuccess?.data?.score
                    val bestMove = evalSuccess?.data?.bestMove
                    val engineAvailable = evalResult is ApiResult.Success

                    val update = QuickCoachLogic.buildUpdateFromEngine(
                        capturedPiece,
                        score,
                        bestMove,
                        liveHint = lastHumanMoveHint,
                        engineAvailable = engineAvailable,
                        classificationOverride = lastHumanMoveClassification,
                        engineSignal = lastHumanMoveEngineSignal,
                    )
                    onQuickCoachUpdate?.invoke(update)
                }
            }
        }
    }

    private fun processAIMoveResult(
        move: AIMove?,
        applyAIMove: (Int, Int, Int, Int) -> Char,
    ): Char? {
        if (turn != Turn.AI) return null

        if (move == null || !move.isValid()) {
            turn = Turn.HUMAN
            return null
        }

        assertTurn(Turn.AI)
        turn = Turn.HUMAN
        val captured = applyAIMove(move.fr, move.fc, move.tr, move.tc)
        moveHistory.add(uciFromCoords(move.fr, move.fc, move.tr, move.tc))
        return captured
    }

    fun reset() {
        invalidateState()
    }

    /**
     * Restore the client-side move history after a HomeActivity Resume
     * tap.  Used by [MainActivity] when the saved snapshot contains a
     * non-empty UCI list — the board's position is restored via
     * [ChessBoardView.setFEN], and this call resyncs the ViewModel so:
     *
     *   - [exportPGN] at the next /game/finish includes the pre-resume
     *     moves (otherwise the PGN would be a stub starting from the
     *     resumed position, which the backend rejects as a tactical
     *     anomaly when the game ends in 2 moves)
     *   - [moveCount] reflects the true half-move number, so the
     *     Atrium chapter header doesn't read "Move 1" after restoring
     *     a 14-move game
     *
     * The native engine is stateless ([ChessNative.getBestMove] is pure
     * FEN → move), so no JNI sync is required.  AI turn is inferred
     * from list parity: even count → HUMAN to move next, odd → AI.
     */
    fun restoreMoveHistory(uciList: List<String>) {
        invalidateState()
        moveHistory.addAll(uciList)
        turn = if (uciList.size % 2 == 0) Turn.HUMAN else Turn.AI
    }
}
