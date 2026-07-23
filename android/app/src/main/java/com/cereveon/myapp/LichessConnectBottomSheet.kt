package com.cereveon.myapp

import android.content.Context
import android.content.SharedPreferences
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.isVisible
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelProvider.NewInstanceFactory
import com.google.android.material.bottomsheet.BottomSheetDialogFragment

/**
 * Cereveon · Atrium · Lichess Connect bottom sheet.
 *
 * Surface invariants (mirror docs/API_CONTRACTS.md §§27–30 + the
 * trust-boundary note from llm/seca/lichess/import_service.py):
 *
 *   - Linking requires proof of ownership: the "Connect" button opens
 *     the Lichess OAuth consent screen via [LichessLinkFlow]; the
 *     backend exchanges the returned code server-side and links the
 *     VERIFIED identity.  No username is typed — a user can only link
 *     an account they actually control (the same PKCE flow as sign-in).
 *
 *   - On link success the calibration banner is shown ONCE — if the
 *     user reopens the sheet later, the GET /lichess/status response
 *     does not carry calibration data, so the banner stays hidden.
 *     This matches the user-question selection ("Inline status + brief
 *     toast"): calibration is surfaced loudly on the transition and
 *     fades to a plain "Linked" state afterwards.
 *
 *   - On import success a Toast surfaces the counts immediately; the
 *     status block updates inline so the row counts increase without a
 *     manual refresh.
 *
 *   - Unlink is the full-trio MVP scope — single tap, server response
 *     drives the transition back to NotLinked.  Imported game_events
 *     rows are retained server-side per the contract.
 *
 * Lifecycle:
 *   - onViewCreated: refresh status from the backend so the sheet
 *     shows the live state, not a stale cache.
 *   - The Fragment owns the ViewModel directly (no shared scope) —
 *     dismissing the sheet tears it down and any in-flight launch is
 *     cancelled cleanly.
 *
 * Mirrors the SettingsBottomSheet idiom: extends
 * [BottomSheetDialogFragment], inflates a layout, binds views in
 * onViewCreated, no Activity scaffolding.
 */
class LichessConnectBottomSheet : BottomSheetDialogFragment() {

    private lateinit var viewModel: LichessConnectViewModel

    // Cached views — bound in onViewCreated.
    private lateinit var loadingSpinner: ProgressBar
    private lateinit var groupNotLinked: View
    private lateinit var groupLinked: View
    private lateinit var btnLink: Button
    private lateinit var btnImport: Button
    private lateinit var btnUnlink: Button
    private lateinit var linkedHandleText: TextView
    private lateinit var calibrationBanner: TextView
    private lateinit var reconnectNotice: TextView
    private lateinit var importedCountText: TextView
    private lateinit var lastSyncedText: TextView

    // v2 async-import progress block — hidden except during Importing.
    private lateinit var importProgressBlock: View
    private lateinit var importProgressBar: ProgressBar
    private lateinit var importProgressCaption: TextView

    /**
     * Test seam — production callers don't pass one and we build the
     * default factory in onCreate; instrumentation tests can swap in a
     * factory backed by a fake [LichessApiClient].
     */
    var viewModelFactoryOverride: ViewModelProvider.Factory? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.bottom_sheet_lichess_connect, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        loadingSpinner = view.findViewById(R.id.lichessLoadingSpinner)
        groupNotLinked = view.findViewById(R.id.groupNotLinked)
        groupLinked = view.findViewById(R.id.groupLinked)
        btnLink = view.findViewById(R.id.btnLichessLink)
        btnImport = view.findViewById(R.id.btnLichessImport)
        btnUnlink = view.findViewById(R.id.btnLichessUnlink)
        linkedHandleText = view.findViewById(R.id.lichessLinkedHandle)
        calibrationBanner = view.findViewById(R.id.lichessCalibrationBanner)
        reconnectNotice = view.findViewById(R.id.lichessReconnectNotice)
        importedCountText = view.findViewById(R.id.lichessImportedCount)
        lastSyncedText = view.findViewById(R.id.lichessLastSyncedValue)
        importProgressBlock = view.findViewById(R.id.lichessImportProgressBlock)
        importProgressBar = view.findViewById(R.id.lichessImportProgressBar)
        importProgressCaption = view.findViewById(R.id.lichessImportProgressCaption)

        val factory = viewModelFactoryOverride ?: defaultFactory(requireContext())
        viewModel = ViewModelProvider(this, factory)[LichessConnectViewModel::class.java]

        viewModel.onStateChanged = { state -> renderState(state) }
        viewModel.onError = { kind -> surfaceError(kind) }

        // Link / Reconnect → prove Lichess ownership via OAuth: the
        // browser opens the consent screen and LichessLinkRedirectActivity
        // completes the link (verified identity, not a typed username).
        // On return, onStart() → refreshStatus() reflects Linked.
        btnLink.setOnClickListener {
            val host = activity ?: return@setOnClickListener
            if (LichessLinkFlow.start(host)) {
                Toast.makeText(host, R.string.lichess_link_opening, Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(host, R.string.lichess_link_no_browser, Toast.LENGTH_LONG).show()
            }
        }
        btnImport.setOnClickListener {
            viewModel.importGames()
        }
        btnUnlink.setOnClickListener {
            viewModel.unlink()
        }
        // refreshStatus is fired from onStart() (below) so it ALSO
        // runs when the user foregrounds the app without re-creating
        // the sheet view — that's the resume-on-reopen path that picks
        // up active_import_job_id from /lichess/status and rejoins the
        // determinate progress bar.
    }

    override fun onStart() {
        super.onStart()
        // Single source of truth for "view is visible → load latest state".
        // Fires on both first-open AND app-foreground-while-sheet-open.
        // If the player has no token (signed out), the ViewModel surfaces
        // UNAUTHENTICATED and the sheet dismisses — sheet requires an
        // authenticated session by construction.
        viewModel.refreshStatus()
    }

    override fun onStop() {
        // Pause the v2 import-job poll loop on background / sheet
        // dismiss so we don't hammer the server while the user isn't
        // looking (and so Doze doesn't punish us on resume).  The
        // server-side job continues independently; the next onStart()
        // fires refreshStatus() which rejoins via active_import_job_id.
        viewModel.pausePolling()
        super.onStop()
    }

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------

    private fun renderState(state: LichessConnectViewModel.UiState) {
        when (state) {
            is LichessConnectViewModel.UiState.Initial -> {
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = false
                groupLinked.isVisible = false
            }
            is LichessConnectViewModel.UiState.Loading -> {
                // Loading is an overlay — render the previous state
                // underneath so the sheet doesn't flash empty.
                renderState(state.previousState)
                loadingSpinner.isVisible = true
                setControlsEnabled(false)
            }
            is LichessConnectViewModel.UiState.NotLinked -> {
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = true
                groupLinked.isVisible = false
                setControlsEnabled(true)
                // A prior disconnected render may have relabelled the
                // link button; a plain not-linked state always reads
                // "Link".
                btnLink.text = getString(R.string.lichess_link_button_label)
            }
            is LichessConnectViewModel.UiState.Linked -> {
                loadingSpinner.isVisible = false
                groupLinked.isVisible = true
                setControlsEnabled(true)
                // Hide the v2 import-progress block when transitioning
                // out of Importing (or just landing on Linked fresh).
                importProgressBlock.isVisible = false

                linkedHandleText.text = state.username
                importedCountText.text = state.importedGameCount.toString()
                lastSyncedText.text =
                    state.lastImportedAt?.let { formatTimestamp(it) }
                        ?: getString(R.string.lichess_never_synced)

                // Reconnect state (API_CONTRACTS §29 disconnected flag):
                // surface the notice AND the Connect button so the user
                // can reconnect (re-prove ownership via OAuth) — history
                // is preserved server-side either way.  Import is hidden
                // while disconnected: the stream can only 404 until a
                // re-link or the account coming back.
                reconnectNotice.isVisible = state.disconnected
                groupNotLinked.isVisible = state.disconnected
                btnImport.isVisible = !state.disconnected
                btnLink.text = getString(
                    if (state.disconnected) R.string.lichess_reconnect_button_label
                    else R.string.lichess_link_button_label,
                )

                // One-shot calibration banner.
                val calibration = state.calibration
                if (calibration != null && calibration.applied) {
                    calibrationBanner.isVisible = true
                    calibrationBanner.text = formatCalibrationBanner(calibration)
                } else {
                    calibrationBanner.isVisible = false
                }

                // One-shot import summary toast.
                state.lastImportSummary?.let { surfaceImportSummary(it) }
            }
            is LichessConnectViewModel.UiState.Importing -> {
                // Render the surrounding Linked context (handle, counts)
                // so the user sees what's being imported into.
                val prior = state.previousLinked
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = false
                groupLinked.isVisible = true
                linkedHandleText.text = prior.username
                importedCountText.text = prior.importedGameCount.toString()
                lastSyncedText.text =
                    prior.lastImportedAt?.let { formatTimestamp(it) }
                        ?: getString(R.string.lichess_never_synced)
                calibrationBanner.isVisible = false
                // An import can only start from a connected state, so
                // the reconnect notice is stale by construction here.
                reconnectNotice.isVisible = false

                // Swap the Import button for the determinate progress
                // block.  Unlink stays enabled so the user can cancel
                // the in-flight job by unlinking (server-side
                // unlink_account cancels the job).
                btnImport.isVisible = false
                importProgressBlock.isVisible = true
                btnImport.isEnabled = false
                btnLink.isEnabled = false
                btnUnlink.isEnabled = true

                // Bind progress values.  ``target`` is the request
                // cap, not the true game count (which we won't know
                // until the stream ends), hence "of up to N" in the
                // caption.  Clamp progress to [0, target] defensively
                // — the server should never send inserted > target,
                // but a transient race could push it over.
                val target = state.target.coerceAtLeast(1)
                val inserted = state.inserted.coerceIn(0, target)
                importProgressBar.max = target
                importProgressBar.progress = inserted

                val skipped = state.skippedDuplicate + state.skippedInvalid
                importProgressCaption.text = if (skipped == 0) {
                    getString(
                        R.string.lichess_import_progress_caption,
                        inserted,
                        target,
                    )
                } else {
                    getString(
                        R.string.lichess_import_progress_caption_with_skipped,
                        inserted,
                        target,
                        skipped,
                    )
                }
            }
            is LichessConnectViewModel.UiState.Error -> {
                // Error state preserves the previous state under the
                // toast.  The ViewModel already reverted to previousState
                // before invoking onError — we just re-render it.
                renderState(state.previousState)
            }
        }
    }

    private fun setControlsEnabled(enabled: Boolean) {
        btnLink.isEnabled = enabled
        btnImport.isEnabled = enabled
        btnUnlink.isEnabled = enabled
    }

    // ------------------------------------------------------------------
    // Error surfacing
    // ------------------------------------------------------------------

    private fun surfaceError(kind: LichessConnectViewModel.ErrorKind) {
        val ctx = context ?: return
        val message = when (kind) {
            LichessConnectViewModel.ErrorKind.UNAUTHENTICATED ->
                getString(R.string.lichess_error_unauthenticated)
            LichessConnectViewModel.ErrorKind.NOT_LINKED ->
                getString(R.string.lichess_error_not_linked)
            LichessConnectViewModel.ErrorKind.RATE_LIMITED ->
                getString(R.string.lichess_error_rate_limited)
            LichessConnectViewModel.ErrorKind.UPSTREAM ->
                getString(R.string.lichess_error_upstream)
            LichessConnectViewModel.ErrorKind.NETWORK ->
                getString(R.string.lichess_error_network)
            LichessConnectViewModel.ErrorKind.TIMEOUT ->
                getString(R.string.lichess_error_timeout)
            LichessConnectViewModel.ErrorKind.UNKNOWN ->
                getString(R.string.lichess_error_unknown)
        }
        Toast.makeText(ctx, message, Toast.LENGTH_LONG).show()
    }

    private fun surfaceImportSummary(summary: LichessImportResponse) {
        val ctx = context ?: return
        val msg = if (summary.inserted > 0) {
            getString(R.string.lichess_import_summary_inserted, summary.inserted)
        } else if (summary.skippedDuplicate > 0) {
            getString(R.string.lichess_import_summary_all_duplicates, summary.skippedDuplicate)
        } else {
            getString(R.string.lichess_import_summary_empty)
        }
        Toast.makeText(ctx, msg, Toast.LENGTH_SHORT).show()
    }

    // ------------------------------------------------------------------
    // Formatting helpers
    // ------------------------------------------------------------------

    private fun formatCalibrationBanner(c: LichessCalibrationResult): String {
        // We still require ``c.rating`` to be non-null before showing
        // anything — that's the marker for "a calibration was applied
        // to this player".  The value itself is no longer rendered:
        // the user-visible Elo display was hidden, so the banner now
        // just confirms the source perf + games-basis.
        c.rating ?: return ""
        val perf = c.perf ?: return ""
        val basis = c.gamesBasis ?: 0
        return if (c.provisional == true) {
            getString(R.string.lichess_calibration_banner_provisional, perf, basis)
        } else {
            getString(R.string.lichess_calibration_banner, perf, basis)
        }
    }

    private fun formatTimestamp(iso: String): String {
        // Backend returns ISO-8601 like "2026-05-13T08:28:57.755000".
        // For the row we just want the date — fine-grained time isn't
        // useful at the user-facing level.
        return iso.substringBefore("T")
    }

    // ------------------------------------------------------------------
    // ViewModel factory — production wiring
    // ------------------------------------------------------------------

    private fun defaultFactory(ctx: Context): ViewModelProvider.Factory {
        val authRepo = AuthRepository(EncryptedTokenStorage(ctx))
        val client: LichessApiClient = HttpLichessApiClient(
            baseUrl = BuildConfig.COACH_API_BASE,
            tokenSink = { newToken -> authRepo.saveToken(newToken) },
        )
        return object : NewInstanceFactory() {
            @Suppress("UNCHECKED_CAST")
            override fun <T : androidx.lifecycle.ViewModel> create(modelClass: Class<T>): T {
                return LichessConnectViewModel(client, authRepo) as T
            }
        }
    }

    companion object {
        /** Tag used by [show] so duplicate sheets can't be opened. */
        const val TAG = "LichessConnectBottomSheet"
    }
}
