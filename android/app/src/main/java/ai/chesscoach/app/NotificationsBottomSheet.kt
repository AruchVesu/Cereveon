package ai.chesscoach.app

import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeParseException

/**
 * Cereveon · Atrium · Notifications feed bottom sheet.
 *
 * Opened from the Home header bell (communication & access spec §5.6,
 * adapted).  Data comes from GET /notifications; rows render in two
 * sections — Unread, then Read — newest first inside each.
 *
 * Interactions:
 *   - Tap a row → mark read server-side, then follow its [NotificationItem.action]
 *     deep-link key ([onOpenHistory] / [onReconnectLichess], wired by the
 *     host).  Rows with no action (or an unknown key from a newer
 *     server) just mark read in place.
 *   - "Mark all read" → POST /notifications/read-all, rows stay listed.
 *   - Per-row ✕ → POST /notifications/{id}/dismiss, row leaves the feed.
 *
 * Every mutation response carries the fresh badge count; the sheet
 * forwards it through [onUnreadCountChanged] so the Home bell badge
 * updates without a second fetch.
 *
 * Mirrors the GameHistoryBottomSheet idiom: BottomSheetDialogFragment +
 * programmatic rows in a LinearLayout + content-hugging resize.
 */
class NotificationsBottomSheet : BottomSheetDialogFragment() {

    /** Injected by the host activity before [show] is called. */
    var notificationsApiClient: NotificationsApiClient? = null
    var authRepository: AuthRepository? = null

    /** Deep-link callbacks — the host owns navigation. */
    var onOpenHistory: (() -> Unit)? = null
    var onReconnectLichess: (() -> Unit)? = null

    /** Fresh badge count after any fetch/mutation — host updates the bell. */
    var onUnreadCountChanged: ((Int) -> Unit)? = null

    private lateinit var notificationsList: LinearLayout
    private lateinit var txtEmpty: TextView
    private lateinit var btnMarkAllRead: TextView

    private var loadJob: Job? = null
    private var latestFeed: List<NotificationItem> = emptyList()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_notifications, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        notificationsList = view.findViewById(R.id.notificationsList)
        txtEmpty = view.findViewById(R.id.txtNotificationsEmpty)
        btnMarkAllRead = view.findViewById(R.id.btnMarkAllRead)

        btnMarkAllRead.setOnClickListener { markAllRead() }
        load()
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    private fun load() {
        val client = notificationsApiClient
        val token = authRepository?.getToken()
        notificationsList.removeAllViews()
        txtEmpty.visibility = View.GONE
        btnMarkAllRead.visibility = View.GONE
        if (client == null || token == null) {
            showEmpty(getString(R.string.notifications_error))
            return
        }
        loadJob?.cancel()
        loadJob = viewLifecycleOwner.lifecycleScope.launch {
            when (val result = client.feed(token)) {
                is ApiResult.Success -> {
                    latestFeed = result.data.notifications
                    onUnreadCountChanged?.invoke(result.data.unreadCount)
                    render()
                }
                else -> showEmpty(getString(R.string.notifications_error))
            }
            resizeSheetToContent()
        }
    }

    private fun markAllRead() {
        val client = notificationsApiClient ?: return
        val token = authRepository?.getToken() ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            when (val result = client.markAllRead(token)) {
                is ApiResult.Success -> {
                    onUnreadCountChanged?.invoke(result.data.unreadCount)
                    latestFeed = latestFeed.map { it.copy(readAt = it.readAt ?: NOW_MARKER) }
                    render()
                    resizeSheetToContent()
                }
                else -> { /* non-fatal; rows keep their fetched state */ }
            }
        }
    }

    private fun markReadAndNavigate(item: NotificationItem) {
        val client = notificationsApiClient
        val token = authRepository?.getToken()
        if (client != null && token != null && !item.isRead) {
            // Fire-and-forget: navigation must not wait on the write.
            viewLifecycleOwner.lifecycleScope.launch {
                val result = client.markRead(item.id, token)
                if (result is ApiResult.Success) {
                    onUnreadCountChanged?.invoke(result.data.unreadCount)
                }
            }
            latestFeed = latestFeed.map {
                if (it.id == item.id) it.copy(readAt = it.readAt ?: NOW_MARKER) else it
            }
        }
        when (item.action) {
            NotificationItem.ACTION_OPEN_HISTORY -> {
                dismiss()
                onOpenHistory?.invoke()
            }
            NotificationItem.ACTION_LICHESS_RECONNECT -> {
                dismiss()
                onReconnectLichess?.invoke()
            }
            else -> {
                // Informational row (or an action key from a newer
                // server) — just re-render with the read state.
                render()
                resizeSheetToContent()
            }
        }
    }

    private fun dismissRow(item: NotificationItem) {
        val client = notificationsApiClient ?: return
        val token = authRepository?.getToken() ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            when (val result = client.dismiss(item.id, token)) {
                is ApiResult.Success -> {
                    onUnreadCountChanged?.invoke(result.data.unreadCount)
                    latestFeed = latestFeed.filterNot { it.id == item.id }
                    render()
                    resizeSheetToContent()
                }
                else -> { /* row stays; next open refetches truth */ }
            }
        }
    }

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------

    private fun render() {
        notificationsList.removeAllViews()
        if (latestFeed.isEmpty()) {
            showEmpty(getString(R.string.notifications_empty))
            return
        }
        txtEmpty.visibility = View.GONE

        val (unread, read) = partitionFeed(latestFeed)
        btnMarkAllRead.visibility = if (unread.isEmpty()) View.GONE else View.VISIBLE

        if (unread.isNotEmpty()) {
            notificationsList.addView(
                buildSectionLabel(getString(R.string.notifications_section_unread, unread.size))
            )
            unread.forEach { item ->
                notificationsList.addView(buildRow(item, isUnread = true))
                notificationsList.addView(buildDivider())
            }
        }
        if (read.isNotEmpty()) {
            notificationsList.addView(
                buildSectionLabel(getString(R.string.notifications_section_read))
            )
            read.forEach { item ->
                notificationsList.addView(buildRow(item, isUnread = false))
                notificationsList.addView(buildDivider())
            }
        }
    }

    private fun showEmpty(message: String) {
        txtEmpty.text = message
        txtEmpty.visibility = View.VISIBLE
        btnMarkAllRead.visibility = View.GONE
    }

    private fun buildSectionLabel(text: String): View {
        val ctx = requireContext()
        return TextView(ctx).apply {
            this.text = text
            textSize = 11f
            typeface = Typeface.MONOSPACE
            letterSpacing = 0.08f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_dim))
            setPadding(0, 22, 0, 10)
        }
    }

    /**
     * One feed row: title line (+ age, right-aligned), body line, and a
     * trailing ✕ dismiss affordance.  Unread rows carry the ink title +
     * a leading accent dot; read rows are muted throughout.
     */
    private fun buildRow(item: NotificationItem, isUnread: Boolean): View {
        val ctx = requireContext()
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.TOP
            setPadding(0, 14, 0, 14)
        }

        val textColumn = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }

        val titleLine = TextView(ctx).apply {
            text = if (isUnread) "● ${item.title}" else item.title
            textSize = 15f
            typeface = Typeface.create("serif", Typeface.ITALIC)
            setTextColor(
                ContextCompat.getColor(
                    ctx,
                    if (isUnread) R.color.atrium_ink else R.color.atrium_muted,
                )
            )
        }
        textColumn.addView(titleLine)

        val bodyLine = TextView(ctx).apply {
            text = item.body
            textSize = 13f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_muted))
            setPadding(0, 4, 0, 0)
        }
        textColumn.addView(bodyLine)

        val metaLine = TextView(ctx).apply {
            val age = formatAge(item.createdAt, System.currentTimeMillis())
            val label = item.actionLabel
            text = if (label.isNullOrBlank()) age else "$age · $label ›"
            textSize = 11f
            typeface = Typeface.MONOSPACE
            letterSpacing = 0.06f
            setTextColor(
                ContextCompat.getColor(
                    ctx,
                    if (item.action != null) R.color.atrium_accent_cyan else R.color.atrium_dim,
                )
            )
            setPadding(0, 6, 0, 0)
        }
        textColumn.addView(metaLine)
        row.addView(textColumn)

        val dismissButton = TextView(ctx).apply {
            text = "✕"
            textSize = 13f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_dim))
            setPadding(24, 4, 8, 24)
            contentDescription = getString(R.string.notifications_dismiss_cd)
            setOnClickListener { dismissRow(item) }
        }
        row.addView(dismissButton)

        row.setOnClickListener { markReadAndNavigate(item) }
        return row
    }

    private fun buildDivider(): View {
        val ctx = requireContext()
        return View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 1)
            setBackgroundColor(ContextCompat.getColor(ctx, R.color.atrium_hairline))
        }
    }

    /**
     * Same content-hugging clamp as GameHistoryBottomSheet — the M3
     * sheet frame doesn't shrink on its own, so re-pin peek/max height
     * to the content after every render.
     */
    private fun resizeSheetToContent() {
        val root = view ?: return
        root.post {
            val dlg = dialog as? com.google.android.material.bottomsheet.BottomSheetDialog
                ?: return@post
            val scrollRegion = notificationsList.parent as? View ?: return@post
            val contentBottom = (
                if (txtEmpty.visibility == View.VISIBLE) txtEmpty.bottom
                else scrollRegion.bottom
                ) + root.paddingBottom
            if (contentBottom <= 0) return@post
            dlg.behavior.maxHeight = contentBottom
            dlg.behavior.peekHeight = contentBottom
        }
    }

    companion object {
        /** Tag used by [show] so duplicate sheets can't be opened. */
        const val TAG = "NotificationsBottomSheet"

        /**
         * Local stand-in for a just-written ``read_at`` so optimistic
         * re-renders can move a row to the Read section without waiting
         * for a refetch.  Never parsed as a timestamp (formatAge only
         * reads ``created_at``).
         */
        internal const val NOW_MARKER = "read-just-now"

        /**
         * Split a feed into (unread, read) keeping the server's
         * newest-first order inside each section.  Pure —
         * unit-testable without Android.
         */
        fun partitionFeed(
            items: List<NotificationItem>,
        ): Pair<List<NotificationItem>, List<NotificationItem>> =
            items.partition { !it.isRead }

        /**
         * Bell-badge label: exact count up to 9, then "9+" so the
         * badge circle never has to grow.  Pure — unit-testable.
         */
        fun formatBadgeLabel(count: Int): String =
            if (count > 9) "9+" else count.toString()

        /**
         * Human age for a row: "now" under a minute, then minutes,
         * hours, days.  [iso] is the backend's naive-UTC ISO-8601
         * string; [nowMillis] is wall-clock UTC millis.  Malformed /
         * missing timestamps render as an empty string rather than
         * crashing the row.  Pure — unit-testable.
         */
        fun formatAge(iso: String?, nowMillis: Long): String {
            if (iso.isNullOrBlank()) return ""
            val createdMillis = try {
                LocalDateTime.parse(iso).toInstant(ZoneOffset.UTC).toEpochMilli()
            } catch (_: DateTimeParseException) {
                return ""
            }
            val deltaMinutes = (nowMillis - createdMillis) / 60_000L
            return when {
                deltaMinutes < 1 -> "now"
                deltaMinutes < 60 -> "${deltaMinutes}m ago"
                deltaMinutes < 60 * 24 -> "${deltaMinutes / 60}h ago"
                else -> "${deltaMinutes / (60 * 24)}d ago"
            }
        }
    }
}
