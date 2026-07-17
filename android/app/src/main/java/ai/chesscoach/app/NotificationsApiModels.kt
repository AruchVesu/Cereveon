package ai.chesscoach.app

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed request/response models for the in-app notification feed
 * (GET /notifications + read / read-all / dismiss mutations).
 *
 * Wire format mirrors the FastAPI shapes documented in
 * docs/API_CONTRACTS.md §40.  ``@SerialName`` keeps the Kotlin
 * properties camelCase while the JSON stays snake_case.
 *
 * Rows are created server-side only (Lichess import completion, the
 * Lichess-disconnected alert) — there is no create request model by
 * design.
 */

/**
 * One feed row.
 *
 * [action] is an app-internal deep-link KEY, not a URL: the client
 * maps [ACTION_OPEN_HISTORY] to the game-history sheet and
 * [ACTION_LICHESS_RECONNECT] to the Lichess connect sheet.  Unknown
 * keys (a newer server) must render as informational rows with no CTA
 * — that contract is what lets new types ship server-first.
 */
@Serializable
data class NotificationItem(
    val id: String,
    val type: String,
    val priority: String = PRIORITY_MEDIUM,
    val title: String,
    val body: String,
    val action: String? = null,
    @SerialName("action_label") val actionLabel: String? = null,
    val metadata: NotificationMetadata = NotificationMetadata(),
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("read_at") val readAt: String? = null,
) {
    val isRead: Boolean get() = readAt != null

    companion object {
        const val TYPE_GAME_ANALYZED = "game_analyzed"
        const val TYPE_SYSTEM_ALERT = "system_alert"

        const val PRIORITY_MEDIUM = "medium"

        const val ACTION_OPEN_HISTORY = "open_history"
        const val ACTION_LICHESS_RECONNECT = "lichess_reconnect"
    }
}

/**
 * Per-type context payload.  All fields optional — each type
 * populates its own subset (``games_analyzed`` for game_analyzed,
 * ``lichess_username`` for the reconnect alert) and
 * ``ignoreUnknownKeys`` tolerates future additions.
 */
@Serializable
data class NotificationMetadata(
    @SerialName("games_analyzed") val gamesAnalyzed: Int? = null,
    @SerialName("lichess_username") val lichessUsername: String? = null,
)

/** Response from GET /notifications. */
@Serializable
data class NotificationsFeedResponse(
    val notifications: List<NotificationItem> = emptyList(),
    /**
     * Bell-badge value: unread visible rows at medium+ priority.  The
     * server computes it so the badge and the feed can't drift on
     * filter semantics.
     */
    @SerialName("unread_count") val unreadCount: Int = 0,
)

/** Response from POST /notifications/{id}/read. */
@Serializable
data class NotificationReadResponse(
    val read: Boolean = false,
    @SerialName("unread_count") val unreadCount: Int = 0,
)

/** Response from POST /notifications/read-all. */
@Serializable
data class NotificationsReadAllResponse(
    val marked: Int = 0,
    @SerialName("unread_count") val unreadCount: Int = 0,
)

/** Response from POST /notifications/{id}/dismiss. */
@Serializable
data class NotificationDismissResponse(
    val dismissed: Boolean = false,
    @SerialName("unread_count") val unreadCount: Int = 0,
)
