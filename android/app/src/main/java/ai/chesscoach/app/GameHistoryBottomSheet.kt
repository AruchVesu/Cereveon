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
import java.time.format.DateTimeFormatter

/**
 * Bottom sheet showing the player's recent games, with an All / In-app /
 * Lichess source filter so imported Lichess games are visible, labelled,
 * and reachable independently of how many recent in-app games exist.
 *
 * Data is fetched from GET /game/history (Bearer auth). The [HistoryFilter]
 * maps each tab to the endpoint's `source` query param + a row limit — the
 * "All" tab keeps the 20-game recent window (so the rating sparkline reads
 * as recent form), while the source-filtered tabs request more so a view
 * isn't truncated by unrelated recent games. Imported rows carry a
 * "LICHESS" badge.
 *
 * Tapping a game loads it onto the main board for replay + live coaching
 * (see [MainActivity.openFinishedGameReview]) and dismisses this sheet.
 * Replay works for every game (positions are replayed server-side from the
 * stored PGN), so every row with an event id is tappable.
 */
class GameHistoryBottomSheet : BottomSheetDialogFragment() {

    /**
     * A history tab: the `source` query value it requests (null = all
     * provenances) and the row [limit] for that view.
     */
    enum class HistoryFilter(val source: String?, val limit: Int) {
        // "All" keeps the recent 20-game window (sparkline = recent form).
        ALL(null, 20),
        // Source views request a deeper slice so they aren't truncated by
        // unrelated recent games of the other provenance.
        APP("app", 50),
        LICHESS("lichess", 50),
    }

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

        /**
         * True when a history row was imported from Lichess (vs played
         * in-app).  Case-insensitive; pure — unit-testable without Android.
         */
        fun isImported(game: GameHistoryItem): Boolean =
            game.source.equals("lichess", ignoreCase = true)
    }

    /** Injected by [MainActivity] before [show] is called. */
    var gameApiClient: GameApiClient? = null

    private lateinit var historyList: LinearLayout
    private lateinit var txtHistoryEmpty: TextView
    private lateinit var sparkline: RatingSparklineView
    private lateinit var filterAll: TextView
    private lateinit var filterApp: TextView
    private lateinit var filterLichess: TextView

    private var selected: HistoryFilter = HistoryFilter.ALL
    private var loadJob: Job? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_game_history, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        historyList = view.findViewById(R.id.historyList)
        txtHistoryEmpty = view.findViewById(R.id.txtHistoryEmpty)
        sparkline = view.findViewById(R.id.ratingSparkline)
        filterAll = view.findViewById(R.id.filterAll)
        filterApp = view.findViewById(R.id.filterApp)
        filterLichess = view.findViewById(R.id.filterLichess)

        filterAll.setOnClickListener { select(HistoryFilter.ALL) }
        filterApp.setOnClickListener { select(HistoryFilter.APP) }
        filterLichess.setOnClickListener { select(HistoryFilter.LICHESS) }

        select(HistoryFilter.ALL)
    }

    private fun select(filter: HistoryFilter) {
        selected = filter
        styleTabs()
        load(filter)
    }

    private fun styleTabs() {
        applyTabStyle(filterAll, selected == HistoryFilter.ALL)
        applyTabStyle(filterApp, selected == HistoryFilter.APP)
        applyTabStyle(filterLichess, selected == HistoryFilter.LICHESS)
    }

    private fun applyTabStyle(tab: TextView, isSelected: Boolean) {
        val ctx = requireContext()
        tab.setBackgroundResource(
            if (isSelected) R.drawable.history_filter_chip_selected
            else R.drawable.history_filter_chip_unselected
        )
        tab.setTextColor(
            ContextCompat.getColor(
                ctx,
                if (isSelected) R.color.atrium_accent_cyan else R.color.atrium_dim,
            )
        )
        tab.setTypeface(null, if (isSelected) Typeface.BOLD else Typeface.NORMAL)
    }

    private fun load(filter: HistoryFilter) {
        val client = gameApiClient
        // Fresh view: clear the previous tab's rows / states before fetching
        // so a slow network can't leave stale rows under the new selection.
        historyList.removeAllViews()
        sparkline.visibility = View.GONE
        txtHistoryEmpty.visibility = View.GONE
        if (client == null) {
            txtHistoryEmpty.text = "No games recorded yet."
            txtHistoryEmpty.visibility = View.VISIBLE
            return
        }

        // Cancel any in-flight load so rapid tab taps can't interleave two
        // render passes into historyList.
        loadJob?.cancel()
        loadJob = lifecycleScope.launch {
            when (val result = client.getGameHistory(source = filter.source, limit = filter.limit)) {
                is ApiResult.Success -> render(result.data, filter)
                else -> {
                    txtHistoryEmpty.text = "Could not load history. Check your connection."
                    txtHistoryEmpty.visibility = View.VISIBLE
                }
            }
        }
    }

    private fun render(games: List<GameHistoryItem>, filter: HistoryFilter) {
        if (games.isEmpty()) {
            txtHistoryEmpty.text = when (filter) {
                HistoryFilter.LICHESS ->
                    "No imported Lichess games yet. Link your account to import them."
                HistoryFilter.APP -> "No in-app games yet."
                HistoryFilter.ALL -> "No games recorded yet."
            }
            txtHistoryEmpty.visibility = View.VISIBLE
            return
        }
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

    /**
     * One history entry: a tappable row (result · accuracy · rating · date)
     * with a trailing "LICHESS" badge on imported games. Tapping opens the
     * full game review — step through the board (◀/▶) with the coaching
     * chat below. Replay works for every game (positions are replayed from
     * the stored PGN); the chat section appears only for games with a
     * linked thread.
     */
    private fun buildGameRow(game: GameHistoryItem): View {
        val ctx = requireContext()
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val label = TextView(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            text = headerText(game)
            setTextColor(resultColor(game))
            textSize = 13f
            typeface = Typeface.MONOSPACE
            setPadding(0, 14, 0, 14)
        }
        row.addView(label)

        if (isImported(game)) {
            row.addView(buildImportedBadge())
        }

        // Every finished game has a replayable position list (keyed by its
        // event id), so every row opens the review.
        if (game.id.isNotBlank() && gameApiClient != null) {
            row.setOnClickListener { openReview(game) }
        }
        return row
    }

    private fun buildImportedBadge(): View {
        val ctx = requireContext()
        return TextView(ctx).apply {
            text = "LICHESS"
            textSize = 9f
            typeface = Typeface.MONOSPACE
            letterSpacing = 0.1f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_accent_cyan))
            setBackgroundResource(R.drawable.lichess_badge_bg)
            setPadding(12, 4, 12, 4)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = 16 }
        }
    }

    private fun openReview(game: GameHistoryItem) {
        // Load the finished game onto the main board for replay + live coaching,
        // then close this sheet so the user lands on the board.
        (activity as? MainActivity)?.openFinishedGameReview(game.id, game.gameId)
        dismiss()
    }

    // Atrium two-tone signal — cyan for wins, amber for losses (warning
    // role), atrium_muted for draws.  Matches the rest of the dashboard.
    private fun resultColor(game: GameHistoryItem): Int {
        val ctx = requireContext()
        return when (game.result.lowercase()) {
            "win"  -> ContextCompat.getColor(ctx, R.color.atrium_accent_cyan)
            "loss" -> ContextCompat.getColor(ctx, R.color.atrium_accent_amber)
            else   -> ContextCompat.getColor(ctx, R.color.atrium_muted)
        }
    }

    private fun headerText(game: GameHistoryItem): String {
        val resultLabel = game.result.uppercase()
        val accuracy = "${(game.accuracy * 100).toInt()}% acc"
        val rating = game.ratingAfter?.let { "  ·  %.0f pts".format(it) } ?: ""
        val lastMove = game.lastMove?.takeIf { it.isNotBlank() }?.let { "  ·  last $it" } ?: ""
        val winnerMove = game.winnerMove?.takeIf { it.isNotBlank() }?.let { "  ·  won $it" } ?: ""
        val date = formatDate(game.createdAt)
        // ▸ signals the row is tappable (opens the full game review).
        return "$resultLabel  ·  $accuracy$rating$lastMove$winnerMove  ▸\n$date"
    }

    private fun buildDivider(): View = View(requireContext()).apply {
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 1
        )
        // atrium_hairline (8% white) — same primitive used by the
        // Atrium.Divider style across the rest of the app.
        setBackgroundColor(
            ContextCompat.getColor(requireContext(), R.color.atrium_hairline)
        )
    }

    private fun formatDate(iso: String): String = try {
        val dt = LocalDateTime.parse(iso, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
        "%02d/%02d  %02d:%02d".format(dt.monthValue, dt.dayOfMonth, dt.hour, dt.minute)
    } catch (_: Exception) {
        iso.take(10)
    }
}
