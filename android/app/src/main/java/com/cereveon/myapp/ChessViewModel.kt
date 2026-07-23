package com.cereveon.myapp

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

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
    /**
     * Optional safe-mode gate.  When non-null, [dispatchHumanMoveCoach]
     * skips the `POST /live/move` call whenever the gate is not in the
     * [SecaSafetyState.Safe] state.  This is the per-move enforcement
     * of the README "before sending coaching requests, confirm
     * safe_mode" contract.  When null, the legacy unconditional
     * behaviour applies (kept for tests that don't care about the
     * gate).
     *
     * The local engine path (C++ via [engineProvider]) is intentionally
     * NOT gated by this — it doesn't reach the backend's coaching
     * pipeline.  Engine eval (`/analyze`, via [engineEvalClient]) is
     * also not gated: it returns the deterministic ESV with no LLM /
     * adaptive surface.  Only the live coaching hint and chat go
     * through the gate.
     */
    var secaSafetyGate: SecaSafetyGate? = null,
    /**
     * Milliseconds to hold the engine's reply before it lands on the board,
     * sampled once per move.  The native engine answers in milliseconds,
     * which reads as a vending machine rather than an opponent — production
     * (the default) paces every playable reply into the
     * [AI_THINK_PACING_MIN_MS]..[AI_THINK_PACING_MAX_MS] window.  Injectable
     * so unit tests that drive the turn loop in real wall-clock time can pass
     * `{ 0L }`; suites on a virtual-time dispatcher advance through the
     * default transparently.
     */
    private val aiThinkPacingMillis: () -> Long = { defaultAiThinkPacingMillis() },
) : ViewModel() {

    /**
     * Supplier of the current SERVER game id (``games.id`` from
     * POST /game/start) for the free-tier coached-game admission —
     * threaded into every /live/move call as ``game_id``
     * (API_CONTRACTS.md §4).  MainActivity wires this to
     * ``currentGameId()``; null (unwired, or no server game yet) keeps
     * today's behaviour — the server fails open and never degrades.
     */
    var serverGameIdProvider: (() -> String?)? = null

    private var turn: Turn = Turn.HUMAN
    private var aiThinking = false

    private var stateId: Long = 0
    private var aiJob: Job? = null

    private var lastHumanMoveHint: String? = null
    private var lastHumanMoveClassification: MistakeClassification? = null
    private var lastHumanMoveEngineSignal: EngineSignalDto? = null

    // ── Move history for PGN export ──────────────────────────────────────────
    private val moveHistory = mutableListOf<String>()

    // The player's (White's) most recent move in UCI, sent to the chat coach so
    // it can describe "your last move" in plain English instead of misreading
    // the raw FEN.  Null until the human has moved this game.
    private var lastHumanUci: String? = null

    /**
     * Called on the Main thread after each AI move with a [QuickCoachUpdate].
     * When [engineEvalClient] is set, the update contains the real Stockfish
     * centipawn score; otherwise the score field is "?" (engine unavailable).
     * When [liveCoachClient] is set, [QuickCoachUpdate.explanation] is the
     * per-move coaching hint from POST /live/move.
     */
    var onQuickCoachUpdate: ((QuickCoachUpdate) -> Unit)? = null

    /**
     * Fires when a move ends the game (checkmate / stalemate).  Invoked by the
     * move handlers AFTER the move is appended to [moveHistory], so a listener
     * calling [exportPGN] sees the final (mating) move.  Wired by [MainActivity].
     */
    var onGameOver: ((GameResult) -> Unit)? = null

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

    /** The player's most recent move (UCI), or null if they haven't moved yet. */
    fun lastHumanMoveUci(): String? = lastHumanUci

    /**
     * Returns the game moves as a well-formed PGN string including the four
     * mandatory headers required by the backend [GameFinishRequest] validator.
     *
     * Without headers the backend raises a 422 "invalid PGN: no PGN headers
     * found" error, silently failing every /game/finish call.
     *
     * [resultTag] is the PGN `Result` header. White is always "Player" and
     * Black "Engine", so a finished game passes "1-0" (player won), "0-1"
     * (engine won) or "1/2-1/2" (draw). The default "*" (unknown) suits
     * in-progress snapshots. The server reads this header to surface the
     * winner's last move in game history — "*" yields no winner move.
     */
    fun exportPGN(resultTag: String = "*"): String {
        if (moveHistory.isEmpty()) return "(no moves)"
        val moves = moveHistory
            .mapIndexed { index, uci ->
                if (index % 2 == 0) "${index / 2 + 1}. $uci" else uci
            }
            .joinToString(" ")
        return """[Event "Chess Coach Game"]
[White "Player"]
[Black "Engine"]
[Result "$resultTag"]

$moves"""
    }

    private fun uciFromCoords(fr: Int, fc: Int, tr: Int, tc: Int, promo: Char = ' '): String {
        val files = "abcdefgh"
        val suffix = if (promo.isLetter()) promo.lowercaseChar().toString() else ""
        return "${files[fc]}${8 - fr}${files[tc]}${8 - tr}$suffix"
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
        lastHumanUci = null
        lastHumanMoveHint = null
        lastHumanMoveClassification = null
        lastHumanMoveEngineSignal = null
        Log.d("STATE", "Game state invalidated. New ID: $stateId")
    }

    fun onHumanMove(
        fr: Int, fc: Int, tr: Int, tc: Int,
        applyHumanMove: () -> MoveResult,
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult? = { null },
    ) {
        if (turn != Turn.HUMAN) return

        val requestId = stateId

        viewModelScope.launch(ioDispatcher) {
            // Capture the pre-move FEN in the same Main hop, BEFORE applying the
            // move (exportFEN() is evaluated first), so the coach can grade move
            // quality from the eval swing fen_before -> post-move fen.
            val (fenBeforeHuman, result) = withContext(Dispatchers.Main) {
                exportFEN() to applyHumanMove()
            }

            withContext(Dispatchers.Main) {
                if (stateId != requestId) return@withContext

                when (result) {
                    MoveResult.SUCCESS -> {
                        val humanUci = uciFromCoords(fr, fc, tr, tc)
                        moveHistory.add(humanUci)
                        lastHumanUci = humanUci
                        // Fire game-over only AFTER recording the move, so
                        // exportPGN() includes the mating move; skip the AI
                        // reply since the game is over.
                        val over = consumeGameOver()
                        if (over != null) {
                            onGameOver?.invoke(over)
                        } else {
                            turn = Turn.AI
                            val fenAfterHuman = exportFEN()
                            dispatchHumanMoveCoach(fenAfterHuman, humanUci, fenBeforeHuman, requestId)
                            requestAIMove(exportFEN, applyAIMove, consumeGameOver)
                        }
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
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult? = { null },
    ) {
        if (turn != Turn.HUMAN) return
        // The human's promotion may itself have ended the game (a queening
        // mate or a stalemating under-promotion): promotePawn now records
        // that, so consume it BEFORE dispatching an AI reply — mirroring
        // the normal-move path in onHumanMove.  Without this the AI would
        // be asked to move in a finished position.
        val over = consumeGameOver()
        if (over != null) {
            onGameOver?.invoke(over)
            return
        }
        turn = Turn.AI
        requestAIMove(exportFEN, applyAIMove, consumeGameOver)
    }

    private fun requestAIMove(
        exportFEN: () -> String,
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult?,
    ) {
        if (aiThinking || turn != Turn.AI) return
        aiThinking = true

        val requestId = stateId

        aiJob = viewModelScope.launch(ioDispatcher) {
            try {
                val fen = withContext(Dispatchers.Main) { exportFEN() }

                // Hybrid cache lookup with a hard ceiling on blocking.
                //
                // The previous implementation called the suspending
                // `getOpponentElo()` directly; that issues
                // `/player/progress` on a cache miss and would wait up
                // to the HTTP read timeout (~15s) when the backend is
                // unhealthy.  During that wait `turn = AI` and the
                // board visually freezes — the symptom users saw when
                // the SECA snackbar showed "Backend safety unverified".
                //
                // Steady-state: warmed by MainActivity at cold-start
                // and after every /game/finish, so the fast path is a
                // cache hit and resolves instantly.  Cold-start race:
                // when the warm hasn't returned yet, fall back to a
                // bounded fetch and degrade to strength 100 after
                // [STRENGTH_FETCH_BUDGET_MS] so the AI dispatch is
                // never blocked for longer than half a second.
                val strengthLevel: Int = playerProfileCache?.let { cache ->
                    cache.cachedOpponentEloOrNull()?.let { return@let EloToStrength.map(it) }
                    val elo = withTimeoutOrNull(STRENGTH_FETCH_BUDGET_MS) {
                        try { cache.getOpponentElo() } catch (_: Exception) { null }
                    }
                    elo?.let { EloToStrength.map(it) } ?: 100
                } ?: 100

                // engineProvider.getBestMove is a JNI call; a bad
                // strength level or a transient native fault must not
                // leave `turn = AI` (board frozen forever).  Catching
                // here lets processAIMoveResult(null, ...) flip turn
                // back to HUMAN so the user can keep playing — same
                // outcome as the native engine returning a no-move.
                val move = try {
                    engineProvider.getBestMove(fen, strengthLevel)
                } catch (t: Throwable) {
                    // Dedicated tag (vs the historic catch-all "AI_TEST"
                    // used by happy-path logs in this file) so an engine
                    // fault is grep-able in production logs without
                    // wading through every routine play log.
                    Log.e("AI_ENGINE", "engineProvider.getBestMove threw", t)
                    null
                }

                // Pacing: hold a playable reply so the opponent reads as
                // thinking — the engine itself answers in milliseconds.
                // Gated on a valid move so an engine fault still flips the
                // turn back to HUMAN immediately (pacing a failure would
                // look like the frozen board the catch above exists to
                // prevent).  delay() keeps the wait cancellable: reset()
                // cancels [aiJob] mid-pacing, and the stateId guard below
                // re-checks after the wait.
                if (move != null && move.isValid()) {
                    delay(aiThinkPacingMillis())
                }

                withContext(Dispatchers.Main) {
                    if (stateId == requestId) {
                        val captured = processAIMoveResult(move, applyAIMove, consumeGameOver)
                        if (captured != null) {
                            // uci is only valid after isValid() passes — compute here
                            val uci = move?.let {
                                uciFromCoords(it.fr, it.fc, it.tr, it.tc, it.promoChar())
                            } ?: ""
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
        fenBefore: String,
        requestId: Long,
    ) {
        val liveClient = liveCoachClient ?: return
        // README contract: confirm safe_mode=true before sending coaching
        // requests.  When the gate is wired and reports Unknown / Unsafe,
        // skip the `/live/move` call rather than fall through to the
        // optimistic path.  Engine eval (dispatched separately) still
        // runs because it's not a coaching request.
        if (secaSafetyGate?.isSafe() == false) {
            Log.d(
                "AI_TEST",
                "dispatchHumanMoveCoach: skipped — SECA gate state=${secaSafetyGate?.state?.value}",
            )
            return
        }
        viewModelScope.launch(ioDispatcher) {
            val liveResult =
                if (uci.length in 4..5) {
                    liveClient.getLiveCoaching(
                        fen,
                        uci,
                        fenBefore = fenBefore,
                        gameId = serverGameIdProvider?.invoke(),
                    )
                } else {
                    null
                }
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
                    // Entitlements posture: over-quota games get the
                    // deterministic hint; surface the chip driver.
                    coachDegraded = liveSuccess?.data?.coachTier?.degraded == true,
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
        applyAIMove: (Int, Int, Int, Int, Char) -> Char,
        consumeGameOver: () -> GameResult?,
    ): Char? {
        if (turn != Turn.AI) return null

        if (move == null || !move.isValid()) {
            turn = Turn.HUMAN
            return null
        }

        assertTurn(Turn.AI)
        turn = Turn.HUMAN
        val promo = move.promoChar()
        val captured = applyAIMove(move.fr, move.fc, move.tr, move.tc, promo)
        moveHistory.add(uciFromCoords(move.fr, move.fc, move.tr, move.tc, promo))
        // AI's move recorded — surface a game-ending AI move now that
        // exportPGN() would include it.
        consumeGameOver()?.let { onGameOver?.invoke(it) }
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

    companion object {
        /**
         * Hard ceiling on how long [requestAIMove] is allowed to wait
         * for [PlayerProfileCache.getOpponentElo] when the cache is
         * cold.  Without this bound the AI dispatch waited up to the
         * underlying HTTP read timeout (~15s) on a slow / unhealthy
         * backend, which left `turn = AI` and made the board appear
         * frozen between human moves.  500ms keeps the interaction
         * snappy and the cold-start fallback to strength 100 is
         * recovered on the next move once the warm completes.
         */
        const val STRENGTH_FETCH_BUDGET_MS: Long = 500L

        /**
         * Opponent "think" pacing window: every playable engine reply is
         * held for a uniform-random duration in this range (sampled per
         * move) before it lands on the board, so the near-instant native
         * engine feels like an opponent taking 2–3 seconds over a move.
         * iOS mirrors these values in PlayViewModel.aiThinkPacing*Nanos —
         * keep the platforms in lock-step.
         */
        const val AI_THINK_PACING_MIN_MS: Long = 2_000L
        const val AI_THINK_PACING_MAX_MS: Long = 3_000L

        /** Production pacing sample — uniform in the window above. */
        internal fun defaultAiThinkPacingMillis(): Long =
            (AI_THINK_PACING_MIN_MS..AI_THINK_PACING_MAX_MS).random()
    }
}
