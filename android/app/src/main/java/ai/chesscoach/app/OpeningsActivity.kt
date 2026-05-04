package ai.chesscoach.app

import android.app.AlertDialog
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import kotlin.math.roundToInt
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Openings · Repertoire (handoff screen #7).
 *
 * Reached from HomeActivity row III ("Openings").  Static surface for
 * this scaffold pass — the [DEFAULT_REPERTOIRE] list mirrors the four
 * lines in the design exactly so the screen reads identically to the
 * handoff mock.
 *
 * When a real `/repertoire` backend lands the wiring becomes:
 *   - Replace [DEFAULT_REPERTOIRE] with a fetch on onCreate
 *   - "Drill active line" POSTs to /repertoire/drill or opens
 *     TrainingSessionBottomSheet pre-seeded with the active opening
 *   - "+" opens an opening picker (search by ECO / first move)
 *
 * For now both buttons toast "coming soon" — honest about the
 * not-yet-wired backend rather than silently failing on tap.
 *
 * Persistence: none.  When the player swaps their active opening the
 * choice would be local + server-side, but until there's a
 * /repertoire endpoint the active line stays at the design's default.
 */
class OpeningsActivity : AppCompatActivity() {

    /** Currently-rendered list — DEFAULT_REPERTOIRE until /repertoire returns. */
    private var currentRepertoire: List<OpeningEntry> = DEFAULT_REPERTOIRE

    private val authRepo: AuthRepository by lazy {
        AuthRepository(EncryptedTokenStorage(this))
    }

    private val gameApiClient: GameApiClient by lazy {
        HttpGameApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            apiKey = BuildConfig.COACH_API_KEY,
            tokenProvider = { authRepo.getToken() },
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_openings)

        val container = findViewById<LinearLayout>(R.id.openingsCardContainer)
        // Render the canonical defaults synchronously so the screen
        // never reads empty during the network round-trip; the fetch
        // below replaces the list when the server response arrives.
        renderRepertoire(container, DEFAULT_REPERTOIRE)
        renderStats(DEFAULT_REPERTOIRE)
        fetchRepertoire(container)

        findViewById<Button>(R.id.btnOpeningsDrill).setOnClickListener {
            val active = currentRepertoire.firstOrNull { it.isActive }
            if (active == null) {
                Toast.makeText(
                    this,
                    "Set an active line first by tapping a card.",
                    Toast.LENGTH_SHORT,
                ).show()
            } else {
                showDrillOutcomeDialog(active)
            }
        }
        findViewById<Button>(R.id.btnOpeningsAdd).setOnClickListener {
            showAddOpeningDialog(findViewById(R.id.openingsCardContainer))
        }
    }

    /**
     * Fetch the player's repertoire from GET /repertoire and re-render.
     * On failure (transport error / non-200) the activity stays on the
     * synchronously-rendered DEFAULT_REPERTOIRE — the user sees a
     * populated screen either way.
     */
    private fun fetchRepertoire(container: LinearLayout) {
        lifecycleScope.launch {
            when (val r = gameApiClient.getRepertoire()) {
                is ApiResult.Success -> {
                    val openings = r.data
                    if (openings.isEmpty()) {
                        // Server returned an empty list (shouldn't
                        // happen — the endpoint substitutes defaults
                        // server-side — but defensive against contract
                        // drift).  Keep the local defaults.
                        return@launch
                    }
                    val mapped = openings
                        .sortedBy { it.ordinal }
                        .map { dto ->
                            OpeningEntry(
                                eco = dto.eco,
                                name = dto.name,
                                line = dto.line,
                                mastery = dto.mastery,
                                isActive = dto.isActive,
                            )
                        }
                    currentRepertoire = mapped
                    renderRepertoire(container, mapped)
                    renderStats(mapped)
                }
                is ApiResult.HttpError -> Log.d("OPENINGS", "GET /repertoire HTTP ${r.code}")
                is ApiResult.NetworkError -> Log.d("OPENINGS", "network error", r.cause)
                ApiResult.Timeout -> Log.d("OPENINGS", "timed out")
            }
        }
    }

    private fun renderRepertoire(container: ViewGroup, entries: List<OpeningEntry>) {
        container.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (entry in entries) {
            val card = inflater.inflate(R.layout.item_opening_card, container, false)
            bindCard(card, entry)
            container.addView(card)
        }
    }

    private fun bindCard(card: View, entry: OpeningEntry) {
        card.findViewById<TextView>(R.id.openingEco).apply {
            text = entry.eco
            setTextColor(
                ContextCompat.getColor(
                    this@OpeningsActivity,
                    if (entry.isActive) R.color.atrium_accent_cyan else R.color.atrium_dim,
                ),
            )
        }
        card.findViewById<TextView>(R.id.openingName).text = entry.name
        card.findViewById<TextView>(R.id.openingLine).text = entry.line
        card.findViewById<View>(R.id.openingActiveBadge).visibility =
            if (entry.isActive) View.VISIBLE else View.GONE

        // Mastery bar — width as a fraction of the parent track via
        // layoutParams.weight on a 0dp-wide child inside the FrameLayout
        // would need extra plumbing; simpler is to set width directly
        // once the parent has been measured.  We use post {} to wait
        // for layout pass.
        val fill = card.findViewById<View>(R.id.openingMasteryFill)
        fill.setBackgroundColor(
            ContextCompat.getColor(
                this,
                if (entry.isActive) R.color.atrium_accent_cyan else R.color.atrium_muted,
            ),
        )
        fill.post {
            val parentWidth = (fill.parent as? View)?.width ?: 0
            val targetWidth = (parentWidth * entry.mastery).roundToInt()
            fill.layoutParams = fill.layoutParams.apply { width = targetWidth }
            fill.requestLayout()
        }

        card.findViewById<TextView>(R.id.openingMasteryPct).apply {
            text = formatMastery(entry.mastery)
            setTextColor(
                ContextCompat.getColor(
                    this@OpeningsActivity,
                    if (entry.isActive) R.color.atrium_accent_cyan else R.color.atrium_dim,
                ),
            )
        }

        // Active card uses the cyan-tinted background; dormant uses
        // the hairline-bordered transparent fill.  Drawable swap (not
        // tint) so the border + fill colours stay paired correctly.
        card.background = ContextCompat.getDrawable(
            this,
            if (entry.isActive) R.drawable.atrium_opening_card_active
            else R.drawable.atrium_opening_card_dormant,
        )

        // Tap → set as active (no-op when already active).  The
        // current card's existing clickable+focusable + the parent's
        // ripple takes care of the visual feedback.
        card.setOnClickListener {
            if (!entry.isActive) {
                callSetActive(entry)
            }
        }
        // Long-press → delete confirmation.  Returning true so the
        // tap listener doesn't also fire.
        card.setOnLongClickListener {
            confirmDeleteOpening(entry)
            true
        }
    }

    /**
     * Promote the tapped opening to active.  The server enforces the
     * "exactly one active" invariant atomically; we re-render with
     * the response so the UI never falls out of sync with the
     * authoritative state.
     */
    private fun callSetActive(entry: OpeningEntry) {
        val container = findViewById<LinearLayout>(R.id.openingsCardContainer)
        lifecycleScope.launch {
            when (val r = gameApiClient.setActiveOpening(entry.eco)) {
                is ApiResult.Success -> applyServerList(container, r.data, "Set active")
                is ApiResult.HttpError -> showFailureToast("HTTP ${r.code}")
                is ApiResult.NetworkError -> showFailureToast("offline")
                ApiResult.Timeout -> showFailureToast("timed out")
            }
        }
    }

    private fun confirmDeleteOpening(entry: OpeningEntry) {
        AlertDialog.Builder(this)
            .setTitle("Delete ${entry.eco}?")
            .setMessage("Remove ${entry.name} from your repertoire.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Delete") { _, _ -> callDeleteOpening(entry) }
            .show()
    }

    private fun callDeleteOpening(entry: OpeningEntry) {
        val container = findViewById<LinearLayout>(R.id.openingsCardContainer)
        lifecycleScope.launch {
            when (val r = gameApiClient.deleteOpening(entry.eco)) {
                is ApiResult.Success -> applyServerList(container, r.data, "Deleted ${entry.eco}")
                is ApiResult.HttpError ->
                    if (r.code == 404) {
                        // Already gone server-side; just refresh.
                        fetchRepertoire(container)
                    } else showFailureToast("HTTP ${r.code}")
                is ApiResult.NetworkError -> showFailureToast("offline")
                ApiResult.Timeout -> showFailureToast("timed out")
            }
        }
    }

    /**
     * Self-rated drill outcome dialog.  Three preset levels map to
     * canonical outcome values:
     *   "Nailed it" → 1.0
     *   "Mostly"    → 0.6
     *   "Forgot it" → 0.2
     * The server applies an EMA step toward the chosen value, so a
     * single drill nudges mastery without whipsawing the bar.
     */
    private fun showDrillOutcomeDialog(active: OpeningEntry) {
        val labels = arrayOf("Nailed it", "Mostly", "Forgot it")
        val outcomes = floatArrayOf(1.0f, 0.6f, 0.2f)
        AlertDialog.Builder(this)
            .setTitle("How did the drill go?")
            .setMessage("Active line: ${active.eco} · ${active.name}")
            .setItems(labels) { _, which ->
                callDrillResult(active, outcomes[which], labels[which])
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun callDrillResult(active: OpeningEntry, outcome: Float, label: String) {
        val container = findViewById<LinearLayout>(R.id.openingsCardContainer)
        lifecycleScope.launch {
            when (val r = gameApiClient.recordDrillResult(active.eco, outcome)) {
                is ApiResult.Success -> applyServerList(
                    container, r.data, "Drill recorded · $label",
                )
                is ApiResult.HttpError -> showFailureToast("HTTP ${r.code}")
                is ApiResult.NetworkError -> showFailureToast("offline")
                ApiResult.Timeout -> showFailureToast("timed out")
            }
        }
    }

    private fun showAddOpeningDialog(container: LinearLayout) {
        val view = LayoutInflater.from(this).inflate(R.layout.dialog_add_opening, null, false)
        val ecoInput  = view.findViewById<TextInputEditText>(R.id.dialogAddOpeningEco)
        val nameInput = view.findViewById<TextInputEditText>(R.id.dialogAddOpeningName)
        val lineInput = view.findViewById<TextInputEditText>(R.id.dialogAddOpeningLine)

        AlertDialog.Builder(this)
            .setTitle("Add opening")
            .setView(view)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Save") { _, _ ->
                val eco = ecoInput.text?.toString()?.trim().orEmpty().uppercase()
                val name = nameInput.text?.toString()?.trim().orEmpty()
                val line = lineInput.text?.toString()?.trim().orEmpty()
                if (eco.isEmpty() || name.isEmpty() || line.isEmpty()) {
                    Toast.makeText(this, "All three fields are required.", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                callAddOpening(container, eco, name, line)
            }
            .show()
    }

    private fun callAddOpening(container: LinearLayout, eco: String, name: String, line: String) {
        lifecycleScope.launch {
            when (val r = gameApiClient.addOpening(eco, name, line)) {
                is ApiResult.Success -> applyServerList(container, r.data, "Added $eco")
                is ApiResult.HttpError -> {
                    val msg = when (r.code) {
                        400 -> "Invalid opening (check the ECO format)"
                        else -> "HTTP ${r.code}"
                    }
                    showFailureToast(msg)
                }
                is ApiResult.NetworkError -> showFailureToast("offline")
                ApiResult.Timeout -> showFailureToast("timed out")
            }
        }
    }

    /**
     * Replace the rendered list with the server's authoritative
     * post-edit response + brief success toast.  Editing endpoints
     * all return the full updated list so we never have to assemble
     * the new state client-side.
     */
    private fun applyServerList(
        container: LinearLayout,
        dtos: List<RepertoireOpeningDto>,
        toastLabel: String,
    ) {
        val mapped = dtos
            .sortedBy { it.ordinal }
            .map { dto ->
                OpeningEntry(
                    eco = dto.eco,
                    name = dto.name,
                    line = dto.line,
                    mastery = dto.mastery,
                    isActive = dto.isActive,
                )
            }
        currentRepertoire = mapped
        renderRepertoire(container, mapped)
        renderStats(mapped)
        Toast.makeText(this, toastLabel, Toast.LENGTH_SHORT).show()
    }

    private fun showFailureToast(reason: String) {
        Toast.makeText(this, "Couldn't update repertoire · $reason", Toast.LENGTH_SHORT).show()
    }

    private fun renderStats(entries: List<OpeningEntry>) {
        findViewById<TextView>(R.id.openingsStatLines).text = entries.size.toString()
        findViewById<TextView>(R.id.openingsStatDepth).text = formatAvgDepth(entries)
        findViewById<TextView>(R.id.openingsStatScore).text = DEFAULT_SCORE_DISPLAY
    }

    /**
     * One opening line in the user's repertoire.  Mirrors the shape a
     * future `/repertoire` response would carry; the mastery field is
     * 0–1 so it doubles as the bar's width fraction.
     */
    data class OpeningEntry(
        val eco: String,
        val name: String,
        val line: String,
        val mastery: Float,
        val isActive: Boolean,
    )

    companion object {
        /**
         * Hardcoded default repertoire matching the design mock 1-for-1
         * so the scaffold reads exactly like the handoff.  Lifted to
         * the companion object so unit tests can inspect the canonical
         * shape without launching the activity.
         */
        val DEFAULT_REPERTOIRE: List<OpeningEntry> = listOf(
            OpeningEntry(
                eco = "C84",
                name = "Ruy Lopez · Closed",
                line = "1.e4 e5 2.♘f3 ♘c6 3.♗b5 a6",
                mastery = 0.78f,
                isActive = true,
            ),
            OpeningEntry(
                eco = "B22",
                name = "Sicilian · Alapin",
                line = "1.e4 c5 2.c3 ♘f6 3.e5 ♘d5",
                mastery = 0.55f,
                isActive = false,
            ),
            OpeningEntry(
                eco = "D02",
                name = "Queen's Pawn · London",
                line = "1.d4 d5 2.♘f3 ♘f6 3.♗f4",
                mastery = 0.42f,
                isActive = false,
            ),
            OpeningEntry(
                eco = "A04",
                name = "Réti opening",
                line = "1.♘f3 d5 2.c4 e6 3.g3",
                mastery = 0.18f,
                isActive = false,
            ),
        )

        /**
         * Win-rate display for the third metric cell.  Backed by a real
         * stat once /game-history grows a per-opening rollup.  Until
         * then the design's hardcoded 68% reads as a reasonable
         * placeholder rather than "—" (which would imply the surface
         * is broken).
         */
        const val DEFAULT_SCORE_DISPLAY = "68%"

        /**
         * Average half-move depth of memorised lines, rounded to nearest
         * integer.  Counts one half-move per token in the line string;
         * the "1." / "2." numbering is stripped by the space-split since
         * the design's lines render moves with " " separators.
         */
        fun formatAvgDepth(entries: List<OpeningEntry>): String {
            if (entries.isEmpty()) return "0"
            val avg = entries.map { it.line.split(" ").size }.average()
            return avg.roundToInt().toString()
        }

        /** Bar percentage label — "%d%%" for 0–100 with a 1% floor / 100% ceiling. */
        fun formatMastery(mastery: Float): String {
            val clamped = mastery.coerceIn(0f, 1f)
            return "${(clamped * 100f).roundToInt()}%"
        }
    }
}
