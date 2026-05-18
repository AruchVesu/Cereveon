package ai.chesscoach.app

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed request/response models for the backend Lichess integration
 * endpoints (POST/DELETE /lichess/link, GET /lichess/status, POST
 * /lichess/import).
 *
 * Wire format mirrors the FastAPI shapes documented in
 * docs/API_CONTRACTS.md §§27–30.  ``@SerialName`` keeps the Kotlin
 * properties camelCase while the JSON stays snake_case.
 *
 * Trust-boundary note (mirrored from llm/seca/lichess/client.py):
 * Lichess's own Stockfish evals are never trusted by the backend, so
 * the import response never carries Lichess-derived eval data — the
 * Android client only sees row counts and the watermark.
 */

/**
 * Request body for POST /lichess/link.  Username is shape-validated
 * server-side (2–30 chars, `[A-Za-z0-9_-]`).  We send whatever the
 * user typed verbatim — the backend canonicalises to lowercase id.
 */
@Serializable
data class LichessLinkRequest(val username: String)

/**
 * Response from POST /lichess/link.
 *
 * On first-link, [calibration] reports whether the player's rating +
 * confidence were seeded from Lichess perf data (rapid > blitz >
 * classical).  Calibration only fires when the player is still at
 * default rating (1200) + confidence (0.5) — non-default players
 * keep their in-app rating.  See LichessCalibrationResult.
 */
@Serializable
data class LichessLinkResponse(
    val platform: String,
    @SerialName("external_username") val externalUsername: String,
    @SerialName("linked_at") val linkedAt: String? = null,
    val calibration: LichessCalibrationResult,
)

/**
 * Calibration outcome on first-link.
 *
 * When [applied] is true, the player's rating/confidence were seeded
 * from the chosen perf — [perf], [rating], [confidence] are populated.
 * When false, [reason] explains why ("player_already_calibrated" or
 * "no_eligible_perf") and the perf/rating/confidence fields are absent
 * from the wire payload (treated as null here).
 */
@Serializable
data class LichessCalibrationResult(
    val applied: Boolean,
    val reason: String? = null,
    val perf: String? = null,
    val rating: Float? = null,
    val confidence: Float? = null,
    @SerialName("games_basis") val gamesBasis: Int? = null,
    val provisional: Boolean? = null,
)

/**
 * Response from GET /lichess/status.
 *
 * Union-shaped on the wire: when [linked] is false the server returns
 * just ``{"linked": false}``; when true the platform/username/counts
 * are populated.  We model the union as one class with all-optional
 * fields and let the [linked] flag drive UI branching.
 */
@Serializable
data class LichessStatusResponse(
    val linked: Boolean,
    val platform: String? = null,
    @SerialName("external_username") val externalUsername: String? = null,
    @SerialName("linked_at") val linkedAt: String? = null,
    @SerialName("last_imported_at") val lastImportedAt: String? = null,
    @SerialName("imported_game_count") val importedGameCount: Int = 0,
)

/**
 * Response from POST /lichess/import.
 *
 * Per the backend contract: a partial-fail mid-stream commits already-
 * inserted rows and returns the counts seen so far; the watermark
 * advances only on a clean iteration.  ``skipped_*`` counts are
 * observability, not errors.
 */
@Serializable
data class LichessImportResponse(
    val inserted: Int = 0,
    @SerialName("skipped_duplicate") val skippedDuplicate: Int = 0,
    @SerialName("skipped_invalid") val skippedInvalid: Int = 0,
    @SerialName("last_imported_at") val lastImportedAt: String? = null,
)

/**
 * Response from DELETE /lichess/link.  ``{"unlinked": true}`` when a
 * link existed and was removed; ``{"unlinked": false}`` when the
 * player had no link (idempotent — not an error).
 */
@Serializable
data class LichessUnlinkResponse(val unlinked: Boolean)
