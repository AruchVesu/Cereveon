package ai.chesscoach.app

import android.graphics.Color
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * Bottom sheet showing the 20 most recent games for the authenticated player.
 *
 * Data is fetched from GET /game/history (Bearer auth). Shows a rating sparkline
 * (when ≥2 rated games exist), result, accuracy, rating-after, and date for each
 * game. Falls back to an empty-state message on network error or no games.
 *
 * Tapping a game expands it inline to reveal that game's coaching chat, loaded
 * lazily from GET /chat/history?game_id= via [coachApiClient]. Rows with no
 * linked game_id (legacy / imported games, or finishes from pre-#230 clients)
 * have no expand affordance.
 */
class GameHistoryBottomSheet : BottomSheetDialogFragment() {

    companion object {
        /**
         * Extract non-null [GameHistoryItem.ratingAfter] values for the sparkline,
         * returned in chronological order (oldest game first).
         *
         * Takes at most the 10 most recent games from [games] (which arrive
         * newest-first from the server), reverses them to chronological order,
         * then filters out null ratings.
         *
         * Pure function — safe to call from unit tests without Android context.
         */
        fun extractSparklineRatings(games: List<GameHistoryItem>): List<Float> =
            games.take(10).reversed().mapNotNull { it.ratingAfter }
    }

    /** Injected by [MainActivity] before [show] is called. */
    var gameApiClient: GameApiClient? = null

    /**
     * Injected by [MainActivity] before [show]. Used to lazily load a past
     * game's coaching chat (GET /chat/history?game_id=) when its row is tapped
     * to expand. Null disables the expand affordance.
     */
    var coachApiClient: CoachApiClient? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_game_history, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val historyList = view.findViewById<LinearLayout>(R.id.historyList)
        val txtHistoryEmpty = view.findViewById<TextView>(R.id.txtHistoryEmpty)
        val sparkline = view.findViewById<RatingSparklineView>(R.id.ratingSparkline)

        val client = gameApiClient
        if (client == null) {
            txtHistoryEmpty.visibility = View.VISIBLE
            return
        }

        lifecycleScope.launch {
            when (val result = client.getGameHistory()) {
                is ApiResult.Success -> {
                    val games = result.data
                    if (games.isEmpty()) {
                        txtHistoryEmpty.visibility = View.VISIBLE
                    } else {
                        val sparkRatings = extractSparklineRatings(games)
                        if (sparkRatings.size >= 2) {
                            sparkline.setRatings(sparkRatings)
                            sparkline.visibility = View.VISIBLE
                        }
                        games.forEach { game ->
                            historyList.addView(buildGameRow(game))
                            historyList.addView(buildDivider())
                        }
                    }
                }
                else -> {
                    txtHistoryEmpty.text = "Could not load history. Check your connection."
                    txtHistoryEmpty.visibility = View.VISIBLE
                }
            }
        }
    }

    /**
     * One history entry: a tappable header (result · accuracy · rating · date)
     * that expands inline to reveal that game's coaching chat. The chat is
     * fetched lazily on first expand (and kept for the row's lifetime) from
     * GET /chat/history?game_id=. Rows with no linked game_id or no
     * [coachApiClient] render as before, with no expand affordance.
     */
    private fun buildGameRow(game: GameHistoryItem): View {
        val ctx = requireContext()
        // Non-blank linked game id (if any) gates the chat-expand affordance.
        val gid = game.gameId?.takeIf { it.isNotBlank() }
        val expandable = gid != null && coachApiClient != null

        val header = TextView(ctx).apply {
            text = headerText(game, expanded = false, expandable = expandable)
            setTextColor(resultColor(game))
            textSize = 13f
            typeface = android.graphics.Typeface.MONOSPACE
            setPadding(0, 14, 0, 14)
        }

        val chatContainer = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(dp(12), 0, 0, dp(10))
        }

        if (gid != null && coachApiClient != null) {
            var loaded = false
            header.setOnClickListener {
                val expanding = chatContainer.visibility == View.GONE
                chatContainer.visibility = if (expanding) View.VISIBLE else View.GONE
                header.text = headerText(game, expanded = expanding, expandable = true)
                if (expanding && !loaded) {
                    loaded = true
                    loadChatInto(chatContainer, gid)
                }
            }
        }

        return LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
            addView(header)
            addView(chatContainer)
        }
    }

    // Atrium two-tone signal — cyan for wins, amber for losses (warning
    // role), atrium_muted for draws.  Matches the rest of the dashboard.
    private fun resultColor(game: GameHistoryItem): Int {
        val ctx = requireContext()
        return when (game.result.lowercase()) {
            "win"  -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_cyan)
            "loss" -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_amber)
            else   -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_muted)
        }
    }

    private fun headerText(
        game: GameHistoryItem,
        expanded: Boolean,
        expandable: Boolean,
    ): String {
        val resultLabel = game.result.uppercase()
        val accuracy = "${(game.accuracy * 100).toInt()}% acc"
        val rating = game.ratingAfter?.let { "  ·  %.0f pts".format(it) } ?: ""
        val date = formatDate(game.createdAt)
        // ▾ when open, ▸ when closed; nothing when there's no chat to show.
        val chevron = when {
            !expandable -> ""
            expanded    -> "  ▾"
            else        -> "  ▸"
        }
        return "$resultLabel  ·  $accuracy$rating$chevron\n$date"
    }

    /**
     * Fetch the game's chat thread and append it under [container]. Shows a
     * transient "Loading…" note, then the turns (read-only) or a muted
     * empty/error note. Runs on [lifecycleScope] so it's cancelled if the
     * sheet is dismissed mid-flight.
     */
    private fun loadChatInto(container: LinearLayout, gameId: String) {
        val coach = coachApiClient ?: return
        val loading = mutedNote("Loading chat…")
        container.addView(loading)
        lifecycleScope.launch {
            val result = coach.getHistory(limit = 50, gameId = gameId)
            container.removeView(loading)
            when (result) {
                is ApiResult.Success -> {
                    val turns = result.data.turns
                    if (turns.isEmpty()) {
                        container.addView(mutedNote("No coaching chat for this game."))
                    } else {
                        turns.forEach { container.addView(buildChatTurn(it)) }
                    }
                }
                else -> container.addView(mutedNote("Could not load this game's chat."))
            }
        }
    }

    private fun buildChatTurn(turn: ChatHistoryTurnDto): TextView {
        val ctx = requireContext()
        val isUser = turn.role == "user"
        val speaker = if (isUser) "You" else "Coach"
        // Coach replies in accent cyan (the coaching voice); the player's own
        // questions in muted grey — same hierarchy as the live chat sheet.
        val color = androidx.core.content.ContextCompat.getColor(
            ctx,
            if (isUser) R.color.atrium_muted else R.color.atrium_accent_cyan,
        )
        return TextView(ctx).apply {
            text = "$speaker:  ${turn.content}"
            setTextColor(color)
            textSize = 12f
            setPadding(0, dp(4), 0, dp(4))
        }
    }

    private fun mutedNote(message: String): TextView {
        val ctx = requireContext()
        return TextView(ctx).apply {
            text = message
            setTextColor(androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_muted))
            textSize = 12f
            setPadding(0, dp(4), 0, dp(4))
        }
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    private fun buildDivider(): View = View(requireContext()).apply {
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 1
        )
        // atrium_hairline (8% white) — same primitive used by the
        // Atrium.Divider style across the rest of the app.
        setBackgroundColor(
            androidx.core.content.ContextCompat.getColor(
                requireContext(), R.color.atrium_hairline,
            )
        )
    }

    private fun formatDate(iso: String): String = try {
        val dt = LocalDateTime.parse(iso, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
        "%02d/%02d  %02d:%02d".format(dt.monthValue, dt.dayOfMonth, dt.hour, dt.minute)
    } catch (_: Exception) {
        iso.take(10)
    }
}
