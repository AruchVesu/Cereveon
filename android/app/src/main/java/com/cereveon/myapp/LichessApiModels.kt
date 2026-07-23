package com.cereveon.myapp

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
 * Request body for POST /lichess/link.  Linking now requires OAuth
 * ownership proof (same PKCE flow as "Sign in with Lichess"), not a
 * self-asserted username: the app forwards the one-time authorization
 * ``code`` + its ``code_verifier``; the backend exchanges them, reads
 * the VERIFIED Lichess identity, and links that.
 */
@Serializable
data class LichessLinkRequest(
    val code: String,
    @SerialName("code_verifier") val codeVerifier: String,
)

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
    /**
     * v2 only: non-null when a Lichess import job is in flight
     * (status ``queued`` or ``running``) for the current player.  The
     * Connect sheet uses this on view-open to rejoin a progress view
     * that survived a sheet dismiss / device restart.  ``null`` on a
     * not-linked response (server omits the field) and on linked
     * responses where no job is active.
     */
    @SerialName("active_import_job_id") val activeImportJobId: String? = null,
    /**
     * Reconnect flow (API_CONTRACTS §29): true once an import 404'd on
     * the linked Lichess account (closed/renamed) and no clean stream
     * has been seen since.  The Connect sheet renders its reconnect
     * state from this; re-linking or the next clean import clears it
     * server-side.  Defaults keep older servers (field absent) reading
     * as connected.
     */
    val disconnected: Boolean = false,
    @SerialName("disconnected_at") val disconnectedAt: String? = null,
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

// ─────────────────────────────────────────────────────────────────────
// v2 async import — replaces synchronous LichessImportResponse on the
// new client.  See docs/API_CONTRACTS.md §31 and the plan note in
// LichessConnectViewModel about the polling lifecycle.
// ─────────────────────────────────────────────────────────────────────

/**
 * 202 response from POST /lichess/import when the client sends
 * ``X-API-Version: 2``.  Carries the freshly-created (or coalesced)
 * job's state — counters are 0 on a brand-new job, non-zero when a
 * concurrent caller already started the import.
 *
 * Shape is identical to [LichessImportJobStatus] so a fake / cached
 * decoder can use either type.  Kept as a separate type for clarity
 * at the call-site (POST returns Accepted, GET returns Status).
 */
@Serializable
data class LichessImportAccepted(
    @SerialName("job_id") val jobId: String,
    val status: String,
    val inserted: Int = 0,
    @SerialName("skipped_duplicate") val skippedDuplicate: Int = 0,
    @SerialName("skipped_invalid") val skippedInvalid: Int = 0,
    @SerialName("target_max_games") val targetMaxGames: Int,
    @SerialName("last_imported_at_ms") val lastImportedAtMs: Long? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/**
 * 200 response from GET /lichess/import/job/{job_id}.  Same shape as
 * [LichessImportAccepted]; the field set is stable across the job's
 * lifecycle.  [status] ∈ {``queued``, ``running``, ``succeeded``,
 * ``failed``}.  [errorMessage] is non-null on ``failed`` only.
 */
@Serializable
data class LichessImportJobStatus(
    @SerialName("job_id") val jobId: String,
    val status: String,
    val inserted: Int = 0,
    @SerialName("skipped_duplicate") val skippedDuplicate: Int = 0,
    @SerialName("skipped_invalid") val skippedInvalid: Int = 0,
    @SerialName("target_max_games") val targetMaxGames: Int,
    @SerialName("last_imported_at_ms") val lastImportedAtMs: Long? = null,
    @SerialName("error_message") val errorMessage: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
) {
    companion object {
        const val STATUS_QUEUED = "queued"
        const val STATUS_RUNNING = "running"
        const val STATUS_SUCCEEDED = "succeeded"
        const val STATUS_FAILED = "failed"
    }

    val isTerminal: Boolean
        get() = status == STATUS_SUCCEEDED || status == STATUS_FAILED
}
