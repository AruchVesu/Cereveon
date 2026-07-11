package ai.chesscoach.app

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed models for the post-game AI review endpoints
 * (POST/GET /game/{event_id}/review) — docs/API_CONTRACTS.md §39/§39a.
 *
 * One response shape serves both endpoints; the three payload sections
 * ([GameReviewResponse.engine], [.moments], [.llm]) are null until
 * their pipeline stage lands, which is exactly the client's wave
 * boundary: render each section the poll tick it becomes non-null.
 *
 * Trust-boundary note (mirrored from llm/seca/review): the eval series
 * arrives BANDED — five player-relative steps, never centipawns — so
 * this client cannot render numeric evals even by accident.  The five
 * strings map 1:1 onto [EvalBandView.Band].
 */

/** Review job/result row.  `status` drives the 2s poll loop. */
@Serializable
data class GameReviewResponse(
    @SerialName("review_id") val reviewId: String,
    @SerialName("event_id") val eventId: String,
    val status: String,
    @SerialName("analysis_version") val analysisVersion: Int = 1,
    @SerialName("review_mode") val reviewMode: String? = null,
    val engine: ReviewEngine? = null,
    val moments: List<ReviewMoment>? = null,
    val llm: ReviewLlm? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    val entitlement: ReviewEntitlement? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("completed_at") val completedAt: String? = null,
) {
    companion object {
        const val STATUS_QUEUED = "queued"
        const val STATUS_RUNNING = "running"
        const val STATUS_ENGINE_DONE = "engine_done"
        const val STATUS_COMPLETE = "complete"
        const val STATUS_FAILED = "failed"
    }

    /** True when polling should stop (nothing further will change). */
    val isTerminal: Boolean
        get() = status == STATUS_COMPLETE || status == STATUS_FAILED
}

/**
 * Wave-2 engine truth.  [bands] has one entry per board position
 * (index 0 = start, index i = after ply i — the same indexing as
 * GET /game/{event_id}/positions, so it zips with the replay list).
 */
@Serializable
data class ReviewEngine(
    val bands: List<String> = emptyList(),
    val accuracy: Float = 0f,
    val counts: ReviewCounts = ReviewCounts(),
    @SerialName("moves_analyzed") val movesAnalyzed: Int = 0,
    @SerialName("player_color") val playerColor: String? = null,
    val plies: Int = 0,
    val meta: ReviewMeta = ReviewMeta(),
)

@Serializable
data class ReviewCounts(
    val blunders: Int = 0,
    val mistakes: Int = 0,
    val inaccuracies: Int = 0,
)

/** PGN-header echo for the review header line; fields absent server-side stay null. */
@Serializable
data class ReviewMeta(
    val white: String? = null,
    val black: String? = null,
    @SerialName("white_elo") val whiteElo: String? = null,
    @SerialName("black_elo") val blackElo: String? = null,
    @SerialName("time_control") val timeControl: String? = null,
    val opening: String? = null,
    val eco: String? = null,
    val date: String? = null,
    val termination: String? = null,
)

/**
 * One critical moment.  [ply] indexes the replay positions list
 * (position AFTER the move at `positions[ply]`, the faced position at
 * `positions[ply - 1]`), which is how "Explore" jumps the main board.
 */
@Serializable
data class ReviewMoment(
    val ply: Int,
    @SerialName("move_number") val moveNumber: Int,
    val san: String,
    @SerialName("moment_type") val momentType: String,
    val phase: String? = null,
    @SerialName("band_before") val bandBefore: String? = null,
    @SerialName("band_after") val bandAfter: String? = null,
    @SerialName("fen_before") val fenBefore: String? = null,
    @SerialName("fen_after") val fenAfter: String? = null,
    @SerialName("clock_remaining_s") val clockRemainingS: Int? = null,
) {
    companion object {
        const val TYPE_BLUNDER = "blunder"
        const val TYPE_MISSED_WIN = "missed_win"
        const val TYPE_MISTAKE = "mistake"
        const val TYPE_PUNISHED_MISTAKE = "punished_mistake"
        const val TYPE_STRATEGIC = "strategic"
    }
}

/** Wave-3 coach texts.  `outcome` semantics per §39. */
@Serializable
data class ReviewLlm(
    val moments: List<ReviewLlmMoment> = emptyList(),
    val verdict: ReviewLlmVerdict? = null,
    val outcome: String = OUTCOME_FULL,
) {
    companion object {
        const val OUTCOME_FULL = "full"
        const val OUTCOME_FALLBACK = "fallback"
        const val OUTCOME_SKIPPED_ENTITLEMENT = "skipped_entitlement"
    }
}

@Serializable
data class ReviewLlmMoment(
    val ply: Int,
    val text: String,
    val source: String = "llm",
)

@Serializable
data class ReviewLlmVerdict(
    val text: String,
    val source: String = "llm",
)

/**
 * Quota snapshot for the upgrade CTA ("2 coach reviews left this
 * month").  All-null limits mean entitlements are not enforced (dev)
 * — treat as unlimited.
 */
@Serializable
data class ReviewEntitlement(
    val metric: String? = null,
    val allowed: Boolean = true,
    val plan: String? = null,
    val limit: Int? = null,
    val used: Int? = null,
    val remaining: Int? = null,
)
