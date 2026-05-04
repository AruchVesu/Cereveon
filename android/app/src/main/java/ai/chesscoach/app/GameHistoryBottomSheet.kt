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

    private fun buildGameRow(game: GameHistoryItem): TextView {
        // Atrium two-tone signal — cyan for player wins, amber for
        // losses (warning role), atrium_muted for draws.  Replaces
        // the previous traffic-light Color.GREEN / RED / YELLOW which
        // never matched the rest of the dashboard.
        val ctx = requireContext()
        val resultLabel = game.result.uppercase()
        val resultColor = when (game.result.lowercase()) {
            "win"  -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_cyan)
            "loss" -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_accent_amber)
            else   -> androidx.core.content.ContextCompat.getColor(ctx, R.color.atrium_muted)
        }
        val accuracy = "${(game.accuracy * 100).toInt()}% acc"
        val rating = game.ratingAfter?.let { "  ·  %.0f pts".format(it) } ?: ""
        val date = formatDate(game.createdAt)

        return TextView(ctx).apply {
            text = "$resultLabel  ·  $accuracy$rating\n$date"
            setTextColor(resultColor)
            textSize = 13f
            typeface = android.graphics.Typeface.MONOSPACE
            setPadding(0, 14, 0, 14)
        }
    }

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
