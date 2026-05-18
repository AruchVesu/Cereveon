package ai.chesscoach.app

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
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import kotlin.math.roundToInt

/**
 * Cereveon · Atrium · Lichess Connect bottom sheet.
 *
 * Surface invariants (mirror docs/API_CONTRACTS.md §§27–30 + the
 * trust-boundary note from llm/seca/lichess/import_service.py):
 *
 *   - Username pre-validated client-side via
 *     [LichessConnectViewModel.isValidUsername]; the same regex the
 *     backend enforces.  An obviously-bad handle is rejected without a
 *     round-trip.
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
    private lateinit var usernameLayout: TextInputLayout
    private lateinit var usernameField: TextInputEditText
    private lateinit var btnLink: Button
    private lateinit var btnImport: Button
    private lateinit var btnUnlink: Button
    private lateinit var linkedHandleText: TextView
    private lateinit var calibrationBanner: TextView
    private lateinit var importedCountText: TextView
    private lateinit var lastSyncedText: TextView

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
        usernameLayout = view.findViewById(R.id.lichessUsernameLayout)
        usernameField = view.findViewById(R.id.lichessUsernameField)
        btnLink = view.findViewById(R.id.btnLichessLink)
        btnImport = view.findViewById(R.id.btnLichessImport)
        btnUnlink = view.findViewById(R.id.btnLichessUnlink)
        linkedHandleText = view.findViewById(R.id.lichessLinkedHandle)
        calibrationBanner = view.findViewById(R.id.lichessCalibrationBanner)
        importedCountText = view.findViewById(R.id.lichessImportedCount)
        lastSyncedText = view.findViewById(R.id.lichessLastSyncedValue)

        val factory = viewModelFactoryOverride ?: defaultFactory(requireContext())
        viewModel = ViewModelProvider(this, factory)[LichessConnectViewModel::class.java]

        viewModel.onStateChanged = { state -> renderState(state) }
        viewModel.onError = { kind -> surfaceError(kind) }

        btnLink.setOnClickListener {
            val raw = usernameField.text?.toString().orEmpty()
            usernameLayout.error = null
            viewModel.link(raw)
        }
        btnImport.setOnClickListener {
            viewModel.importGames()
        }
        btnUnlink.setOnClickListener {
            viewModel.unlink()
        }

        // Initial fetch.  If the player has no token (signed out), the
        // ViewModel surfaces UNAUTHENTICATED and we dismiss — sheet
        // requires an authenticated session by construction.
        viewModel.refreshStatus()
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
            }
            is LichessConnectViewModel.UiState.Linked -> {
                loadingSpinner.isVisible = false
                groupNotLinked.isVisible = false
                groupLinked.isVisible = true
                setControlsEnabled(true)

                linkedHandleText.text = state.username
                importedCountText.text = state.importedGameCount.toString()
                lastSyncedText.text =
                    state.lastImportedAt?.let { formatTimestamp(it) }
                        ?: getString(R.string.lichess_never_synced)

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
        usernameField.isEnabled = enabled
    }

    // ------------------------------------------------------------------
    // Error surfacing
    // ------------------------------------------------------------------

    private fun surfaceError(kind: LichessConnectViewModel.ErrorKind) {
        val ctx = context ?: return
        val message = when (kind) {
            LichessConnectViewModel.ErrorKind.UNAUTHENTICATED ->
                getString(R.string.lichess_error_unauthenticated)
            LichessConnectViewModel.ErrorKind.USERNAME_INVALID -> {
                usernameLayout.error = getString(R.string.lichess_error_username_invalid)
                return  // inline-only; no toast
            }
            LichessConnectViewModel.ErrorKind.USERNAME_NOT_FOUND ->
                getString(R.string.lichess_error_username_not_found)
            LichessConnectViewModel.ErrorKind.ALREADY_LINKED_TO_OTHER_PLAYER ->
                getString(R.string.lichess_error_already_linked)
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
        val rating = c.rating?.roundToInt() ?: return ""
        val perf = c.perf ?: return ""
        val basis = c.gamesBasis ?: 0
        return if (c.provisional == true) {
            getString(R.string.lichess_calibration_banner_provisional, rating, perf, basis)
        } else {
            getString(R.string.lichess_calibration_banner, rating, perf, basis)
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
