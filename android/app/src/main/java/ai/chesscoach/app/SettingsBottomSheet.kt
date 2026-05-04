package ai.chesscoach.app

import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.slider.Slider
import kotlin.math.roundToInt
import kotlinx.coroutines.launch

/**
 * Cereveon · Atrium · Settings (handoff screen #10).
 *
 * Sections (each separated by an Atrium hairline rule):
 *   1.  Coach voice  — radio (formal / conversational / terse)
 *   2.  Board style  — radio (flat / engraved / wireframe)
 *   3.  Sound        — switch
 *   4.  Notifications — switch
 *   5.  Profile      — chevron row: Skill rating (opens edit dialog)
 *   6.  Premium      — chevron row: Upgrade
 *   7.  Account      — chevron rows: Change password, Sign out
 *
 * Persistence: [PREFS_NAME] SharedPreferences (the same store
 * MainActivity uses for the rating cache and curriculum chip).
 *
 * **Consumer wiring status:**
 *   - Coach voice — persisted and read by `chat_pipeline.generate_chat_reply`
 *     via the `coach_voice` field on `/coach/chat`; CoachApiClient
 *     forwards [readCoachVoice] on every request.
 *   - Board style — persisted and read by [MainActivity.onCreate] /
 *     [MainActivity.onResume]; assigns [ChessBoardView.boardStyle] which
 *     branches the per-square render in `onDraw`.
 *   - Sound / notifications persist, but no audio system or
 *     notification channel exists yet to consume them.
 *
 * The settings UI is the right place to put these toggles ahead of the
 * features that read them, so users see one consistent surface.  The
 * downstream readers can opt-in via [readCoachVoice], [readBoardStyle],
 * [readSoundEnabled], [readNotificationsEnabled].
 */
class SettingsBottomSheet : BottomSheetDialogFragment() {

    /**
     * Optional callbacks the host activity can wire to handle
     * Account-section taps.  Both default to no-ops; MainActivity
     * sets them to forward to its existing change-password dialog
     * and logout flow.
     */
    var onChangePasswordTapped: (() -> Unit)? = null
    var onSignOutTapped: (() -> Unit)? = null

    private val voiceDots = mutableMapOf<String, View>()
    private val boardDots = mutableMapOf<String, View>()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_settings, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val prefs = requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        // ── Coach voice radio ────────────────────────────────────────
        voiceDots["formal"]         = view.findViewById(R.id.voiceFormalDot)
        voiceDots["conversational"] = view.findViewById(R.id.voiceConversationalDot)
        voiceDots["terse"]          = view.findViewById(R.id.voiceTerseDot)
        applyRadioState(voiceDots, prefs.getString(PREF_COACH_VOICE, DEFAULT_COACH_VOICE)!!)

        bindRow(view, R.id.voiceFormal,         voiceDots, PREF_COACH_VOICE)
        bindRow(view, R.id.voiceConversational, voiceDots, PREF_COACH_VOICE)
        bindRow(view, R.id.voiceTerse,          voiceDots, PREF_COACH_VOICE)

        // ── Board style radio ────────────────────────────────────────
        boardDots["flat"]      = view.findViewById(R.id.boardFlatDot)
        boardDots["engraved"]  = view.findViewById(R.id.boardEngravedDot)
        boardDots["wireframe"] = view.findViewById(R.id.boardWireframeDot)
        applyRadioState(boardDots, prefs.getString(PREF_BOARD_STYLE, DEFAULT_BOARD_STYLE)!!)

        bindRow(view, R.id.boardFlat,      boardDots, PREF_BOARD_STYLE)
        bindRow(view, R.id.boardEngraved,  boardDots, PREF_BOARD_STYLE)
        bindRow(view, R.id.boardWireframe, boardDots, PREF_BOARD_STYLE)

        // ── Sound switch ─────────────────────────────────────────────
        val sound = view.findViewById<SwitchCompat>(R.id.switchSound)
        sound.isChecked = prefs.getBoolean(PREF_SOUND_ENABLED, true)
        sound.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_SOUND_ENABLED, checked).apply()
        }
        view.findViewById<View>(R.id.rowSound).setOnClickListener { sound.toggle() }

        // ── Notifications switch ─────────────────────────────────────
        val notif = view.findViewById<SwitchCompat>(R.id.switchNotifications)
        notif.isChecked = prefs.getBoolean(PREF_NOTIFICATIONS_ENABLED, true)
        notif.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(PREF_NOTIFICATIONS_ENABLED, checked).apply()
        }
        view.findViewById<View>(R.id.rowNotifications).setOnClickListener { notif.toggle() }

        // ── Profile · Skill rating chevron row ──────────────────────
        val ratingValueLabel = view.findViewById<TextView>(R.id.rowEditRatingValue)
        ratingValueLabel.text = formatRatingLabel(prefs)
        view.findViewById<View>(R.id.rowEditRating).setOnClickListener {
            showEditRatingDialog(prefs, ratingValueLabel)
        }

        // ── Premium chevron row ──────────────────────────────────────
        view.findViewById<View>(R.id.rowUpgrade).setOnClickListener {
            // Dismiss the sheet first so the paywall slides in over a
            // settled MainActivity background, not over a half-faded
            // bottom-sheet scrim.
            dismiss()
            startActivity(Intent(requireContext(), PaywallActivity::class.java))
        }

        // ── Account chevron rows ─────────────────────────────────────
        view.findViewById<View>(R.id.rowChangePassword).setOnClickListener {
            dismiss()
            onChangePasswordTapped?.invoke()
        }
        view.findViewById<View>(R.id.rowSignOut).setOnClickListener {
            dismiss()
            onSignOutTapped?.invoke()
        }
    }

    /**
     * Show the rating-edit AlertDialog, prefilled with the user's
     * current calibrated rating.  On Save:
     *   - Persist the new value to PREF_PLAYER_RATING_ESTIMATE +
     *     mirror into PREF_RATING (the drawer header reads it)
     *   - Update the row's trailing label
     *   - Fire PATCH /auth/me on lifecycleScope (best-effort; the
     *     same recovery path MainActivity already runs at cold-start
     *     reconciles if this fails offline)
     *
     * Cancel is a no-op.  No "are you sure" — the slider already
     * shows the new value before tap, and an accidental change is
     * one tap away from a corrective edit.
     */
    private fun showEditRatingDialog(
        prefs: android.content.SharedPreferences,
        rowLabel: TextView,
    ) {
        val ctx = requireContext()
        val view = LayoutInflater.from(ctx).inflate(R.layout.dialog_edit_rating, null, false)
        val slider = view.findViewById<Slider>(R.id.dialogRatingSlider)
        val valueLabel = view.findViewById<TextView>(R.id.dialogRatingValue)

        val current = prefs.getFloat(
            OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
            prefs.getFloat(MainActivity.PREF_RATING, OnboardingActivity.DEFAULT_RATING),
        ).coerceIn(SLIDER_MIN, SLIDER_MAX)

        slider.value = current
        valueLabel.text = OnboardingActivity.formatRating(current)
        slider.addOnChangeListener { _, value, _ ->
            valueLabel.text = OnboardingActivity.formatRating(value)
        }

        AlertDialog.Builder(ctx)
            .setTitle("Adjust your rating")
            .setView(view)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Save") { _, _ ->
                val newRating = slider.value
                persistRating(prefs, newRating)
                rowLabel.text = formatRatingLabel(prefs)
                firePatchAuthMe(newRating)
            }
            .show()
    }

    private fun persistRating(prefs: android.content.SharedPreferences, rating: Float) {
        prefs.edit()
            .putFloat(OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE, rating)
            // Mirror into the drawer-header key so MainActivity picks
            // it up immediately without waiting for the next /auth/me
            // round-trip.
            .putFloat(MainActivity.PREF_RATING, rating)
            .apply()
    }

    private fun firePatchAuthMe(rating: Float) {
        val ctx = requireContext()
        val authRepo = AuthRepository(EncryptedTokenStorage(ctx))
        val token = authRepo.getToken() ?: return
        val client: AuthApiClient = HttpAuthApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        // Fire on the fragment's lifecycleScope; if the user dismisses
        // the sheet before the call returns, the launch is cancelled
        // cleanly.  Confidence omitted: the user only adjusted the
        // rating, not the certainty around it.
        lifecycleScope.launch {
            when (val r = client.updateMe(token, rating = rating, confidence = null)) {
                is ApiResult.Success -> Toast.makeText(
                    ctx, "Rating saved · ${rating.roundToInt()}", Toast.LENGTH_SHORT,
                ).show()
                is ApiResult.HttpError -> {
                    Log.w("SETTINGS", "PATCH /auth/me HTTP ${r.code}")
                    if (r.code != 401) {
                        // 401 → MainActivity's session-expired flow handles it
                        Toast.makeText(
                            ctx,
                            "Saved locally · server sync will retry",
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                }
                is ApiResult.NetworkError -> {
                    Log.w("SETTINGS", "PATCH /auth/me network error", r.cause)
                    Toast.makeText(
                        ctx, "Saved locally · server sync will retry",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
                ApiResult.Timeout -> {
                    Log.w("SETTINGS", "PATCH /auth/me timed out")
                    Toast.makeText(
                        ctx, "Saved locally · server sync will retry",
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            }
        }
    }

    /**
     * Wire a radio-row click: write [prefKey] = row.tag and update
     * the visual selection so only the tapped dot is filled.
     */
    private fun bindRow(
        root: View,
        rowId: Int,
        dots: Map<String, View>,
        prefKey: String,
    ) {
        val row = root.findViewById<LinearLayout>(rowId)
        val value = row.tag as String
        row.setOnClickListener {
            requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putString(prefKey, value).apply()
            applyRadioState(dots, value)
        }
    }

    /** Set the dot drawable for each entry in [dots]: filled if its key matches [selected]. */
    private fun applyRadioState(dots: Map<String, View>, selected: String) {
        val ctx = requireContext()
        val filled = ContextCompat.getDrawable(ctx, R.drawable.atrium_radio_selected)
        val hollow = ContextCompat.getDrawable(ctx, R.drawable.atrium_radio_unselected)
        dots.forEach { (key, dot) ->
            dot.background = if (key == selected) filled else hollow
        }
    }

    companion object {
        // Same SharedPreferences store MainActivity uses for rating
        // cache + curriculum chip.  One prefs file keeps the app's
        // user-state surface coherent.
        const val PREFS_NAME = "chesscoach_prefs"

        const val PREF_COACH_VOICE = "setting_coach_voice"
        const val DEFAULT_COACH_VOICE = "conversational"

        const val PREF_BOARD_STYLE = "setting_board_style"
        const val DEFAULT_BOARD_STYLE = "flat"

        const val PREF_SOUND_ENABLED = "setting_sound_enabled"
        const val PREF_NOTIFICATIONS_ENABLED = "setting_notifications_enabled"

        // Slider bounds for the rating-edit dialog.  Match the
        // OnboardingActivity slider so a re-edit feels like the
        // same affordance the user saw at calibration.
        const val SLIDER_MIN = 800f
        const val SLIDER_MAX = 2600f

        /**
         * Format the current rating for the row's trailing label.
         * Prefers the local PREF_PLAYER_RATING_ESTIMATE (set by
         * Onboarding + this dialog), falls back to PREF_RATING
         * (the drawer-header cache, possibly synced from /auth/me),
         * and finally to the slider's neutral midpoint default
         * so the row never reads "—" once a user is logged in.
         */
        fun formatRatingLabel(prefs: android.content.SharedPreferences): String {
            val rating = prefs.getFloat(
                OnboardingActivity.PREF_PLAYER_RATING_ESTIMATE,
                prefs.getFloat(MainActivity.PREF_RATING, OnboardingActivity.DEFAULT_RATING),
            )
            return OnboardingActivity.formatRating(rating)
        }

        // ── Reader helpers — call these from downstream features ──

        fun readCoachVoice(ctx: Context): String =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(PREF_COACH_VOICE, DEFAULT_COACH_VOICE)!!

        fun readBoardStyle(ctx: Context): String =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(PREF_BOARD_STYLE, DEFAULT_BOARD_STYLE)!!

        fun readSoundEnabled(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_SOUND_ENABLED, true)

        fun readNotificationsEnabled(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_NOTIFICATIONS_ENABLED, true)
    }
}
