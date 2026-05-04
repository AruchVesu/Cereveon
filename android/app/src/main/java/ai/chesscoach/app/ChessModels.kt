package ai.chesscoach.app

/**
 * Result of a move attempt.
 * Extracted from View to allow JVM testing without Android View dependencies.
 */
enum class MoveResult { SUCCESS, PROMOTION, FAILED }

/**
 * Kotlin AIMove model
 * Used by JNI to return coordinates.
 */
data class AIMove(
    val fr: Int,
    val fc: Int,
    val tr: Int,
    val tc: Int
) {
    fun isValid() = fr >= 0
}

/**
 * Mistake severity for the Quick Coach dock.
 * Derived purely from captured material — no inference, no RL.
 */
enum class MistakeClassification {
    GOOD, INACCURACY, MISTAKE, BLUNDER;
    fun label(): String = name
}

/**
 * Structured update emitted after each AI move for the Quick Coach dock.
 *
 * [scoreText]       Formatted score shown in the dock (e.g. "+1.52", "Equal", "?").
 *                   When built from the engine, this is the centipawn evaluation
 *                   formatted by [QuickCoachLogic.formatCentipawns]; when built
 *                   from local material balance it uses [QuickCoachLogic.formatScore].
 * [classification]  Severity of the human's last move.
 * [explanation]     Coaching hint for the position; null when position is solid.
 *                   When the live coaching pipeline is wired, this is the hint
 *                   from POST /live/move; otherwise a static derived string.
 * [bestMove]        Engine's preferred response in UCI notation (e.g. "e2e4");
 *                   null when no engine call was made or engine unavailable.
 * [engineAvailable] False when the engine eval call failed (Timeout / NetworkError /
 *                   HttpError).  True in all other cases, including when
 *                   [engineEvalClient] is null (score is shown as "?").
 */
data class QuickCoachUpdate(
    val scoreText: String,
    val classification: MistakeClassification,
    /** null when position is solid — dock shows fallback text. */
    val explanation: String?,
    /** null when built from local heuristic or when engine is unavailable. */
    val bestMove: String? = null,
    /** False when the eval request failed; used to show the ⚠ indicator. */
    val engineAvailable: Boolean = true,
    /**
     * Engine context signal from POST /live/move; null when the live coaching
     * pipeline is not wired or the backend omitted the field.
     * Reuses [EngineSignalDto] from the chat pipeline so display logic is shared.
     */
    val engineSignal: EngineSignalDto? = null,
    /**
     * True when this update carries the coaching hint for the human's own move
     * (fired immediately after the human moves, before the AI replies).
     * False for AI-score updates emitted after the engine evaluates the position.
     * Only human-move updates should be added to the move classification history.
     */
    val isHumanMoveCoachUpdate: Boolean = false,
)
