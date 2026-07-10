package ai.chesscoach.app

import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Bottom sheet showing the post-game AI review of one imported Lichess
 * game (docs/API_CONTRACTS.md §39/§39a), opened from the replay nav
 * bar's "Coach review" button while the game is on the main board.
 *
 * Rendering is progressive ("three waves"): on open the sheet POSTs
 * /game/{event_id}/review (idempotent — an existing review answers
 * immediately) and then polls every [POLL_INTERVAL_MS] while the job
 * is non-terminal, re-rendering whatever stages have landed:
 *
 *   Wave 1 — meta line from the row (instant).
 *   Wave 2 — banded eval graph + stats + moment cards (engine stage).
 *   Wave 3 — coach texts + verdict (LLM stage).
 *
 * "Explore" on a moment card (and tapping a graph marker) jumps the
 * main-board replay to that ply via [onExplorePly] and dismisses the
 * sheet — from there the existing replay + coach-chat surface takes
 * over ("Ask coach" is the ordinary Coach button on that position).
 *
 * Entitlement UX: past the free cap the review still shows the full
 * engine waves; the LLM section renders the quota line + an UPGRADE
 * action instead (`llm.outcome == "skipped_entitlement"`).  A
 * degraded LLM outcome (`"fallback"`) offers "TRY COACH REVIEW
 * AGAIN", which re-POSTs — the server re-runs only the LLM stage.
 */
class GameReviewBottomSheet : BottomSheetDialogFragment() {

    companion object {
        const val POLL_INTERVAL_MS = 2_000L

        /** Sheet-level action derived from a review payload.  Pure. */
        enum class ReviewAction { NONE, RETRY_COACH, UPGRADE, RETRY_FAILED }

        fun actionFor(review: GameReviewResponse): ReviewAction = when {
            review.status == GameReviewResponse.STATUS_FAILED -> ReviewAction.RETRY_FAILED
            review.status != GameReviewResponse.STATUS_COMPLETE -> ReviewAction.NONE
            review.llm?.outcome == ReviewLlm.OUTCOME_SKIPPED_ENTITLEMENT -> ReviewAction.UPGRADE
            review.llm?.outcome == ReviewLlm.OUTCOME_FALLBACK -> ReviewAction.RETRY_COACH
            else -> ReviewAction.NONE
        }

        /** Status line under the header.  Pure. */
        fun statusLine(review: GameReviewResponse): String = when (review.status) {
            GameReviewResponse.STATUS_QUEUED,
            GameReviewResponse.STATUS_RUNNING,
            -> "Analyzing the game…"
            GameReviewResponse.STATUS_ENGINE_DONE -> "Coach is looking at the game…"
            GameReviewResponse.STATUS_FAILED ->
                "The review could not be completed. Try again."
            else -> when (review.llm?.outcome) {
                ReviewLlm.OUTCOME_SKIPPED_ENTITLEMENT ->
                    "Engine review ready. Coach commentary is a Pro feature this month."
                ReviewLlm.OUTCOME_FALLBACK ->
                    "Review ready (coach used quick notes this time)."
                else -> "Review ready."
            }
        }

        /** "vs opponent1234 (1234) · 600+5 · Ruy Lopez" — from meta.  Pure. */
        fun metaLine(engine: ReviewEngine?): String {
            if (engine == null) return ""
            val meta = engine.meta
            val playerIsWhite = !engine.playerColor.equals("black", ignoreCase = true)
            val opponent = (if (playerIsWhite) meta.black else meta.white) ?: "opponent"
            val opponentElo = (if (playerIsWhite) meta.blackElo else meta.whiteElo)
                ?.let { " ($it)" } ?: ""
            val timeControl = meta.timeControl?.let { " · $it" } ?: ""
            val opening = meta.opening?.let { " · $it" } ?: ""
            return "vs $opponent$opponentElo$timeControl$opening"
        }

        /** "61% acc · 1 blunder · 0 mistakes · 2 inaccuracies".  Pure. */
        fun statsLine(engine: ReviewEngine): String {
            val acc = (engine.accuracy * 100).toInt()
            val c = engine.counts
            return "$acc% acc · ${c.blunders} blunder${plural(c.blunders)} · " +
                "${c.mistakes} mistake${plural(c.mistakes)} · " +
                "${c.inaccuracies} inaccurac${if (c.inaccuracies == 1) "y" else "ies"}"
        }

        private fun plural(n: Int) = if (n == 1) "" else "s"

        /** Card header: "MOVE 11 · MIDDLEGAME".  Pure. */
        fun momentHeader(moment: ReviewMoment): String {
            val phase = moment.phase?.uppercase() ?: "MOMENT"
            return "MOVE ${moment.moveNumber} · $phase"
        }

        /** Band words for the transition line.  Pure. */
        fun bandPhrase(band: String?): String = when (band) {
            "losing" -> "clearly losing"
            "worse" -> "somewhat worse"
            "equal" -> "level"
            "better" -> "somewhat better"
            "winning" -> "clearly winning"
            else -> "unclear"
        }

        /** "level → clearly losing".  Pure. */
        fun momentTransition(moment: ReviewMoment): String =
            "${bandPhrase(moment.bandBefore)} → ${bandPhrase(moment.bandAfter)}"

        /** Quota line, or null when nothing useful to show.  Pure. */
        fun quotaLine(entitlement: ReviewEntitlement?): String? {
            val remaining = entitlement?.remaining ?: return null
            val limit = entitlement.limit ?: return null
            return "$remaining of $limit coach reviews left this month."
        }
    }

    /** Injected by [MainActivity] before [show] is called. */
    var reviewApiClient: ReviewApiClient? = null
    var tokenProvider: (() -> String?)? = null
    var eventId: String? = null

    /** Jump the main-board replay to a ply (Explore / marker tap). */
    var onExplorePly: ((Int) -> Unit)? = null

    private lateinit var txtMeta: TextView
    private lateinit var txtStatus: TextView
    private lateinit var graph: ReviewEvalGraphView
    private lateinit var txtStats: TextView
    private lateinit var momentsList: LinearLayout
    private lateinit var txtVerdictHeader: TextView
    private lateinit var txtVerdict: TextView
    private lateinit var txtQuota: TextView
    private lateinit var btnAction: Button

    private var pollJob: Job? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_game_review, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        txtMeta = view.findViewById(R.id.txtReviewMeta)
        txtStatus = view.findViewById(R.id.txtReviewStatus)
        graph = view.findViewById(R.id.reviewEvalGraph)
        txtStats = view.findViewById(R.id.txtReviewStats)
        momentsList = view.findViewById(R.id.reviewMomentsList)
        txtVerdictHeader = view.findViewById(R.id.txtVerdictHeader)
        txtVerdict = view.findViewById(R.id.txtVerdict)
        txtQuota = view.findViewById(R.id.txtReviewQuota)
        btnAction = view.findViewById(R.id.btnReviewAction)

        graph.onMomentTap = { ply -> explore(ply) }
        txtStatus.text = "Starting the review…"
        startReviewAndPoll()
    }

    private fun explore(ply: Int) {
        onExplorePly?.invoke(ply)
        dismiss()
    }

    /**
     * POST once (idempotent server-side), then poll GET while the row
     * is non-terminal.  Scoped to the view lifecycle so a poll tick
     * can never touch detached views.
     */
    private fun startReviewAndPoll() {
        val client = reviewApiClient
        val token = tokenProvider?.invoke()
        val id = eventId
        if (client == null || token.isNullOrBlank() || id.isNullOrBlank()) {
            txtStatus.text = "The review is unavailable right now."
            return
        }
        pollJob?.cancel()
        pollJob = viewLifecycleOwner.lifecycleScope.launch {
            when (val started = client.startReview(id, token)) {
                is ApiResult.Success -> render(started.data)
                is ApiResult.HttpError -> {
                    renderStartError(started)
                    return@launch
                }
                else -> {
                    txtStatus.text = "Couldn't reach the coach. Check your connection."
                    return@launch
                }
            }
            while (isActive) {
                delay(POLL_INTERVAL_MS)
                val current = tokenProvider?.invoke() ?: token
                when (val polled = client.getReview(id, current)) {
                    is ApiResult.Success -> {
                        render(polled.data)
                        if (polled.data.isTerminal) return@launch
                    }
                    // Transient poll failures keep trying until the view
                    // goes away; the server job advances regardless.
                    else -> Unit
                }
            }
        }
    }

    private fun renderStartError(error: ApiResult.HttpError) {
        txtStatus.text = when {
            error.code == 400 && error.body?.contains("too_short") == true ->
                "This game was too short for a review. Try a longer game."
            error.code == 400 && error.body?.contains("not_lichess") == true ->
                "Reviews are available for imported Lichess games."
            error.code == 429 ->
                "Too many requests — give it a few seconds and try again."
            else -> "The review is unavailable right now (error ${error.code})."
        }
    }

    private fun render(review: GameReviewResponse) {
        txtStatus.text = statusLine(review)

        val engine = review.engine
        if (engine != null) {
            txtMeta.text = metaLine(engine)
            txtStats.text = statsLine(engine)
            txtStats.visibility = View.VISIBLE
            if (engine.bands.size >= 2) {
                graph.setData(
                    engine.bands,
                    review.moments.orEmpty().map {
                        ReviewEvalGraphView.Marker(it.ply, it.momentType)
                    },
                )
                graph.visibility = View.VISIBLE
            }
        }

        renderMoments(review)
        renderVerdict(review)
        renderQuotaAndAction(review)
    }

    private fun renderMoments(review: GameReviewResponse) {
        val moments = review.moments.orEmpty()
        momentsList.removeAllViews()
        if (moments.isEmpty()) return
        val textsByPly = review.llm?.moments.orEmpty().associateBy { it.ply }
        moments.forEachIndexed { index, moment ->
            momentsList.addView(
                buildMomentCard(
                    index = index,
                    total = moments.size,
                    moment = moment,
                    coachText = textsByPly[moment.ply]?.text,
                    llmSettled = review.llm != null ||
                        review.status == GameReviewResponse.STATUS_FAILED,
                )
            )
        }
    }

    private fun buildMomentCard(
        index: Int,
        total: Int,
        moment: ReviewMoment,
        coachText: String?,
        llmSettled: Boolean,
    ): View {
        val ctx = requireContext()
        val card = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            // Hairline card, no corner ticks — ticks are reserved for the
            // single "featured" card of a surface (Home drill card).
            background = AtriumCardBackground(ctx, showTicks = false)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(8) }
        }

        val negative = ReviewEvalGraphView.isNegativeType(moment.momentType)
        val accent = ContextCompat.getColor(
            ctx,
            when {
                negative -> R.color.atrium_accent_amber
                moment.momentType == ReviewMoment.TYPE_PUNISHED_MISTAKE ->
                    R.color.atrium_accent_cyan
                else -> R.color.atrium_muted
            },
        )

        card.addView(TextView(ctx).apply {
            text = "● ${momentHeader(moment)}  ·  ${index + 1}/$total"
            typeface = Typeface.MONOSPACE
            textSize = 10f
            letterSpacing = 0.1f
            setTextColor(accent)
        })

        card.addView(TextView(ctx).apply {
            text = "You played ${moment.san}   (${momentTransition(moment)})"
            typeface = Typeface.MONOSPACE
            textSize = 12f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_ink))
            setPadding(0, dp(6), 0, 0)
        })

        card.addView(TextView(ctx).apply {
            text = when {
                coachText != null -> coachText
                llmSettled -> "" // settled with no text for this card — hide below
                else -> "Coach is looking at this moment…"
            }
            setTextAppearance(R.style.Atrium_Body)
            setPadding(0, dp(6), 0, 0)
            visibility =
                if (coachText == null && llmSettled) View.GONE else View.VISIBLE
        })

        card.addView(TextView(ctx).apply {
            text = "EXPLORE ▸"
            typeface = Typeface.MONOSPACE
            textSize = 11f
            letterSpacing = 0.1f
            setTextColor(ContextCompat.getColor(ctx, R.color.atrium_accent_cyan))
            setPadding(0, dp(10), 0, 0)
            setOnClickListener { explore(moment.ply) }
        })

        return card
    }

    private fun renderVerdict(review: GameReviewResponse) {
        val verdict = review.llm?.verdict?.text
        if (!verdict.isNullOrBlank()) {
            txtVerdictHeader.visibility = View.VISIBLE
            txtVerdict.text = verdict
            txtVerdict.visibility = View.VISIBLE
        }
    }

    private fun renderQuotaAndAction(review: GameReviewResponse) {
        val quota = quotaLine(review.entitlement)
        txtQuota.text = quota ?: ""
        txtQuota.visibility = if (quota != null) View.VISIBLE else View.GONE

        when (actionFor(review)) {
            ReviewAction.RETRY_COACH -> showAction("TRY COACH REVIEW AGAIN")
            ReviewAction.RETRY_FAILED -> showAction("RETRY REVIEW")
            ReviewAction.UPGRADE -> showAction("UPGRADE FOR COACH REVIEWS") {
                startActivity(Intent(requireContext(), PaywallActivity::class.java))
            }
            ReviewAction.NONE -> btnAction.visibility = View.GONE
        }
    }

    private fun showAction(label: String, onClick: (() -> Unit)? = null) {
        btnAction.text = label
        btnAction.visibility = View.VISIBLE
        btnAction.setOnClickListener {
            btnAction.visibility = View.GONE
            if (onClick != null) onClick() else startReviewAndPoll()
        }
    }

    private fun dp(v: Int): Int =
        (v * resources.displayMetrics.density).toInt()
}
