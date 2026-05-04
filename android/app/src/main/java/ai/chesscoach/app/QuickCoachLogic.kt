package ai.chesscoach.app

import kotlin.math.abs

/**
 * Pure-Kotlin logic for the Quick Coach dock.
 *
 * All computation is deterministic:
 *  - Material balance from board state (piece-count heuristic)
 *  - Score formatted as "+1.5", "Equal", "-2.0"
 *  - Capture classification: captured piece value → severity tier
 *  - One-line explanation derived from classification tier
 *
 * No model inference, no RL, no backend calls.
 */
object QuickCoachLogic {

    private val PIECE_VALUE = mapOf(
        'p' to 1, 'n' to 3, 'b' to 3, 'r' to 5, 'q' to 9
    )

    /**
     * Compute material balance (white minus black, in pawn units).
     * Positive = white advantage; negative = black advantage.
     */
    fun materialBalance(board: Array<CharArray>): Float {
        var white = 0f
        var black = 0f
        for (row in board) {
            for (ch in row) {
                val value = PIECE_VALUE[ch.lowercaseChar()]?.toFloat() ?: continue
                if (ch.isUpperCase()) white += value else black += value
            }
        }
        return white - black
    }

    /**
     * Format a material balance float as "+1.5", "Equal", or "-2.0".
     * Values within ±0.05 are considered equal.
     */
    fun formatScore(balance: Float): String = when {
        abs(balance) < 0.05f -> "Equal"
        balance > 0f         -> "+%.1f".format(balance)
        else                 -> "%.1f".format(balance)
    }

    /**
     * Classify the human's last move based on what the AI captured.
     * '.' or any unmapped char → GOOD (AI took nothing).
     */
    fun classifyCapture(capturedPiece: Char): MistakeClassification {
        return when (PIECE_VALUE[capturedPiece.lowercaseChar()] ?: 0) {
            9    -> MistakeClassification.BLUNDER     // queen hung
            5    -> MistakeClassification.MISTAKE     // rook hung
            3    -> MistakeClassification.MISTAKE     // bishop or knight hung
            1    -> MistakeClassification.INACCURACY  // pawn dropped
            else -> MistakeClassification.GOOD
        }
    }

    /**
     * Derive a one-line coaching explanation from the classification.
     * Returns null for GOOD moves — the dock shows a generic fallback instead.
     */
    fun deriveExplanation(classification: MistakeClassification): String? = when (classification) {
        MistakeClassification.BLUNDER    -> "Piece left undefended — engine capitalised."
        MistakeClassification.MISTAKE    -> "Material lost. Protect pieces before advancing."
        MistakeClassification.INACCURACY -> "A pawn dropped. Keep all pieces covered."
        MistakeClassification.GOOD       -> null
    }

    /**
     * Build a [QuickCoachUpdate] from the AI's captured piece and the
     * board state after the AI's move has been applied.
     *
     * Uses the local material balance heuristic for the score.
     * [bestMove] is null because no engine call is made here.
     */
    fun buildUpdate(capturedPiece: Char, board: Array<CharArray>): QuickCoachUpdate {
        val classification = classifyCapture(capturedPiece)
        val balance = materialBalance(board)
        return QuickCoachUpdate(
            scoreText = formatScore(balance),
            classification = classification,
            explanation = deriveExplanation(classification),
        )
    }

    /**
     * Format a centipawn score from the engine as a human-readable string.
     *
     * The engine returns centipawns from White's perspective (100 cp = 1 pawn).
     * Positive → White advantage; negative → Black advantage.
     *
     * | Input        | Output   |
     * |--------------|----------|
     * | null         | "?"      |
     * | abs(cp) < 5  | "Equal"  |
     * | cp = +152    | "+1.52"  |
     * | cp = -80     | "-0.80"  |
     */
    fun formatCentipawns(score: Int?): String = when {
        score == null        -> "?"
        score in -4..4      -> "Equal"
        score > 0           -> "+%.2f".format(score / 100.0)
        else                -> "%.2f".format(score / 100.0)
    }

    /**
     * Build a [QuickCoachUpdate] using the real engine centipawn score
     * instead of the local material balance heuristic.
     *
     * Use this path when [EngineEvalClient.evaluate] has returned successfully.
     *
     * @param capturedPiece    Piece char captured by the AI (or '.' for none).
     * @param engineScore      Centipawn score from [EngineEvalResponse.score]; null if unavailable.
     * @param bestMove         UCI string from [EngineEvalResponse.bestMove]; null if unavailable.
     * @param liveHint         Coaching hint from POST /live/move; overrides the static
     *                         [deriveExplanation] when non-null.
     * @param engineAvailable  False when the eval request failed; propagated to the update
     *                         so the UI can display a degraded-mode indicator.
     */
    fun buildUpdateFromEngine(
        capturedPiece: Char,
        engineScore: Int?,
        bestMove: String? = null,
        liveHint: String? = null,
        engineAvailable: Boolean = true,
        classificationOverride: MistakeClassification? = null,
        engineSignal: EngineSignalDto? = null,
        isHumanMoveCoachUpdate: Boolean = false,
    ): QuickCoachUpdate {
        val classification = classificationOverride ?: classifyCapture(capturedPiece)
        return QuickCoachUpdate(
            scoreText = formatCentipawns(engineScore),
            classification = classification,
            explanation = liveHint ?: deriveExplanation(classification),
            bestMove = bestMove,
            engineAvailable = engineAvailable,
            engineSignal = engineSignal,
            isHumanMoveCoachUpdate = isHumanMoveCoachUpdate,
        )
    }

    /**
     * Map the backend's move_quality string to a [MistakeClassification].
     *
     * The backend returns one of: "GOOD", "INACCURACY", "MISTAKE", "BLUNDER"
     * (or legacy "best" / "ok" treated as GOOD).
     * Any unrecognised string falls back to GOOD (fail-safe).
     */
    fun fromBackendString(s: String): MistakeClassification = when (s.uppercase()) {
        "BLUNDER"    -> MistakeClassification.BLUNDER
        "MISTAKE"    -> MistakeClassification.MISTAKE
        "INACCURACY" -> MistakeClassification.INACCURACY
        else         -> MistakeClassification.GOOD
    }
}
