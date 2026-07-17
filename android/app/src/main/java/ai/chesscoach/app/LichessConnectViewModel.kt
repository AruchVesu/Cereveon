package ai.chesscoach.app

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * State machine for the Lichess Connect bottom sheet.
 *
 * Lifecycle:
 *   - Sheet opens → [refreshStatus] → fetches GET /lichess/status →
 *     transitions to [Linked] or [NotLinked].
 *   - User taps "Link" with a username → [link] → POST /lichess/link →
 *     [Linked] (with optional calibration details).
 *   - User taps "Import games" → [importGames] → POST /lichess/import →
 *     [Linked] with the new counts merged in.
 *   - User taps "Unlink" → [unlink] → DELETE /lichess/link → [NotLinked].
 *
 * Surface invariants:
 *   - Loading is a pure-overlay state — the previous state is preserved
 *     in [previousState] so the UI can render skeleton bones on top of
 *     the last known data instead of flashing empty.
 *   - Errors are non-terminal: after surfacing the message, the
 *     callback re-emits the prior state so the sheet remains usable
 *     (mirrors the toast-on-failure pattern in SettingsBottomSheet
 *     when PATCH /auth/me fails).
 *
 * Callback shape (`onStateChanged`) matches [ChessViewModel]'s
 * mutable-state + callback convention rather than StateFlow — the
 * codebase is callback-first; introducing reactive streams just for
 * this screen would be churn.
 */
class LichessConnectViewModel(
    private val client: LichessApiClient,
    private val authRepository: AuthRepository,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {

    /**
     * Sealed state model.
     *
     * - [Initial]    — sheet opened; no fetch yet.
     * - [Loading]    — fetch in flight.  [previousState] is whatever was
     *                  visible before the request so the UI can render
     *                  it under a spinner.
     * - [NotLinked]  — no Lichess link for this player.  Form is shown.
     * - [Linked]     — link present.  Status fields populated.  Optional
     *                  [calibration] is non-null only on the
     *                  immediately-post-link transition so the sheet can
     *                  display "Rating set to 1907 from rapid" once.
     * - [Error]      — last operation failed.  Carries a user-presentable
     *                  message.  [previousState] holds whatever to revert
     *                  to once the user dismisses the toast.
     */
    sealed class UiState {
        object Initial : UiState()
        data class Loading(val previousState: UiState) : UiState()
        object NotLinked : UiState()
        data class Linked(
            val username: String,
            val linkedAt: String? = null,
            val lastImportedAt: String? = null,
            val importedGameCount: Int = 0,
            val calibration: LichessCalibrationResult? = null,
            val lastImportSummary: LichessImportResponse? = null,
            /**
             * Reconnect flow (API_CONTRACTS §29): true when the server
             * has recorded that the linked account 404'd on import
             * (closed/renamed) with no clean stream since.  The sheet
             * renders the reconnect notice + re-link form on top of
             * the linked status block.
             */
            val disconnected: Boolean = false,
        ) : UiState()
        /**
         * v2 import in flight.  [target] is the request cap (denominator
         * for the progress bar); [inserted] is the live counter.  The
         * UI renders ``inserted / target`` with a `<` sign when not
         * terminal since the true total games could be less than
         * [target] (we don't know up-front).
         *
         * [previousLinked] is the state the sheet should revert to if
         * the user dismisses + the job ends without the sheet seeing
         * the terminal poll (only relevant for short edge cases — the
         * normal terminal path overwrites this state with the merged
         * Linked).
         */
        data class Importing(
            val jobId: String,
            val inserted: Int,
            val target: Int,
            val skippedDuplicate: Int,
            val skippedInvalid: Int,
            val previousLinked: Linked,
        ) : UiState()
        data class Error(val message: String, val previousState: UiState) : UiState()
    }

    /**
     * Operation-level error categories.  The Fragment maps these to
     * concrete user-facing strings so the ViewModel stays
     * resource-free (host-JVM unit-testable).
     */
    enum class ErrorKind {
        UNAUTHENTICATED,
        USERNAME_INVALID,
        USERNAME_NOT_FOUND,
        ALREADY_LINKED_TO_OTHER_PLAYER,
        NOT_LINKED,
        RATE_LIMITED,
        UPSTREAM,
        NETWORK,
        TIMEOUT,
        UNKNOWN,
    }

    private var current: UiState = UiState.Initial

    /**
     * Reference to the currently-active v2 import poll loop, if any.
     * Cancelled before any new long-lived state transition (link /
     * unlink / re-import / explicit refresh) so we never have two
     * polling coroutines for the same player.  ``viewModelScope`` would
     * cancel it on Fragment dismiss anyway; this field handles the
     * intra-lifecycle case.
     */
    private var importPollJob: Job? = null

    /** Latest UiState; primarily for test assertions. */
    val state: UiState get() = current

    /**
     * Invoked on every state transition.  The Fragment binds this in
     * onViewCreated and re-renders.  Single-listener by design: there
     * is exactly one bottom sheet at a time.
     */
    var onStateChanged: ((UiState) -> Unit)? = null

    /**
     * Invoked when an operation fails so the Fragment can surface a
     * Toast / inline error string.  Mapped from the underlying HTTP
     * or transport error.
     */
    var onError: ((ErrorKind) -> Unit)? = null

    // ----------------------------------------------------------------------
    // Public API — Fragment calls these in response to user input
    // ----------------------------------------------------------------------

    fun refreshStatus() {
        viewModelScope.launch { performStatus() }
    }

    fun link(username: String) {
        viewModelScope.launch { performLink(username) }
    }

    fun importGames(maxGames: Int = LichessApiClient.DEFAULT_MAX_IMPORT) {
        viewModelScope.launch { performImport(maxGames) }
    }

    fun unlink() {
        viewModelScope.launch { performUnlink() }
    }

    /**
     * Cancel any in-flight v2 import-job poll loop.
     *
     * Called by the Fragment from ``onStop()`` so that polling pauses
     * when the app is backgrounded — Doze mode would otherwise hammer
     * the server with bursts on resume.  The server-side job continues
     * independently; the next ``refreshStatus()`` call (typically fired
     * from the Fragment's ``onStart()``) reattaches via the
     * ``active_import_job_id`` field on ``/lichess/status``.
     *
     * Safe to call when no poll is in flight (no-op).  Leaves [state]
     * untouched — the UI keeps rendering the last-seen [UiState.Importing]
     * counters until refresh.
     */
    fun pausePolling() {
        importPollJob?.cancel()
        importPollJob = null
    }

    // ----------------------------------------------------------------------
    // Operation handlers
    // ----------------------------------------------------------------------

    private suspend fun performStatus() {
        val token = requireToken() ?: return
        // Any leftover poll from a prior view-binding must not race with
        // the resume path we may kick off below.
        importPollJob?.cancel()
        importPollJob = null

        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) { client.status(token) }) {
            is ApiResult.Success -> {
                val s = result.data
                if (!s.linked) {
                    transitionTo(UiState.NotLinked)
                    return
                }
                val linked = UiState.Linked(
                    username = s.externalUsername ?: "",
                    linkedAt = s.linkedAt,
                    lastImportedAt = s.lastImportedAt,
                    importedGameCount = s.importedGameCount,
                    disconnected = s.disconnected,
                )
                transitionTo(linked)

                // v2 resume: if the server reports an in-flight import
                // for this player, fetch the job once and either rejoin
                // the progress view (running) or render the terminal
                // counters once (already finished by the time we
                // returned).  Either way, polling continues for
                // non-terminal results.
                val activeJobId = s.activeImportJobId
                if (activeJobId != null) {
                    when (val jobResult = withContext(ioDispatcher) {
                        client.getImportJob(activeJobId, token)
                    }) {
                        is ApiResult.Success -> applyJobSnapshot(
                            jobId = activeJobId,
                            status = jobResult.data.status,
                            inserted = jobResult.data.inserted,
                            skippedDuplicate = jobResult.data.skippedDuplicate,
                            skippedInvalid = jobResult.data.skippedInvalid,
                            target = jobResult.data.targetMaxGames,
                            priorLinked = linked,
                            startPolling = true,
                        )
                        // Failure modes (404 / transport / 5xx) on the
                        // resume GET leave the user in the Linked state
                        // we already transitioned to.  No retry — the
                        // user can tap Import again or pull-to-refresh.
                        else -> {}
                    }
                }
            }
            is ApiResult.HttpError -> surfaceHttpError(result.code, "status")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    private suspend fun performLink(username: String) {
        val trimmed = username.trim()
        if (!isValidUsername(trimmed)) {
            surfaceErrorKind(ErrorKind.USERNAME_INVALID)
            return
        }
        val token = requireToken() ?: return
        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) { client.link(trimmed, token) }) {
            is ApiResult.Success -> {
                val data = result.data
                transitionTo(
                    UiState.Linked(
                        username = data.externalUsername,
                        linkedAt = data.linkedAt,
                        lastImportedAt = null,
                        importedGameCount = 0,
                        calibration = data.calibration,
                    )
                )
            }
            is ApiResult.HttpError -> surfaceHttpError(result.code, "link")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    private suspend fun performImport(maxGames: Int) {
        val token = requireToken() ?: return
        val priorLinked = current as? UiState.Linked
        if (priorLinked == null) {
            surfaceErrorKind(ErrorKind.NOT_LINKED)
            return
        }
        // A stale poll from a prior import would otherwise keep flipping
        // counters under our feet; cancel before starting a new one.
        importPollJob?.cancel()
        importPollJob = null

        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) {
            client.startImport(token = token, maxGames = maxGames)
        }) {
            is ApiResult.Success -> {
                val accepted = result.data
                applyJobSnapshot(
                    jobId = accepted.jobId,
                    status = accepted.status,
                    inserted = accepted.inserted,
                    skippedDuplicate = accepted.skippedDuplicate,
                    skippedInvalid = accepted.skippedInvalid,
                    target = accepted.targetMaxGames,
                    priorLinked = priorLinked,
                    startPolling = true,
                )
            }
            is ApiResult.HttpError -> surfaceHttpError(result.code, "import")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    /**
     * Render a job-state snapshot into the UiState machine.
     *
     * Two callers: [performImport] (initial POST response) and the
     * poll loop in [startImportPoll].  Terminal statuses end the
     * polling (caller checks the return); non-terminal transitions
     * to [UiState.Importing] and conditionally schedules the next
     * poll tick.
     *
     * Returns ``true`` if the job is terminal (caller should stop
     * polling), ``false`` if polling should continue.
     */
    private fun applyJobSnapshot(
        jobId: String,
        status: String,
        inserted: Int,
        skippedDuplicate: Int,
        skippedInvalid: Int,
        target: Int,
        priorLinked: UiState.Linked,
        startPolling: Boolean,
    ): Boolean {
        when (status) {
            LichessImportJobStatus.STATUS_SUCCEEDED -> {
                // Merge counts into the prior Linked state.  The
                // ``lastImportedAt`` ISO string is NOT refreshed here
                // (the v2 payload carries Unix ms, not ISO); the next
                // refreshStatus() call will pull the canonical value
                // from the server.  This keeps the response shape
                // boundary clean.
                transitionTo(
                    priorLinked.copy(
                        importedGameCount = priorLinked.importedGameCount + inserted,
                        lastImportSummary = LichessImportResponse(
                            inserted = inserted,
                            skippedDuplicate = skippedDuplicate,
                            skippedInvalid = skippedInvalid,
                            lastImportedAt = priorLinked.lastImportedAt,
                        ),
                        calibration = null,
                    )
                )
                return true
            }
            LichessImportJobStatus.STATUS_FAILED -> {
                // Revert + surface error.  Use ``UPSTREAM`` because the
                // job failure is a server-side problem (Lichess upstream,
                // unlink-mid-import, etc.) rather than a transport issue.
                current = priorLinked
                onStateChanged?.invoke(priorLinked)
                onError?.invoke(ErrorKind.UPSTREAM)
                return true
            }
            else -> {
                transitionTo(
                    UiState.Importing(
                        jobId = jobId,
                        inserted = inserted,
                        target = target,
                        skippedDuplicate = skippedDuplicate,
                        skippedInvalid = skippedInvalid,
                        previousLinked = priorLinked,
                    )
                )
                if (startPolling) {
                    startImportPoll(jobId, priorLinked)
                }
                return false
            }
        }
    }

    private fun startImportPoll(jobId: String, priorLinked: UiState.Linked) {
        importPollJob?.cancel()
        importPollJob = viewModelScope.launch {
            while (isActive) {
                delay(POLL_INTERVAL_MS)
                val token = authRepository.getToken() ?: return@launch
                val result = withContext(ioDispatcher) { client.getImportJob(jobId, token) }
                when (result) {
                    is ApiResult.Success -> {
                        val job = result.data
                        val terminal = applyJobSnapshot(
                            jobId = jobId,
                            status = job.status,
                            inserted = job.inserted,
                            skippedDuplicate = job.skippedDuplicate,
                            skippedInvalid = job.skippedInvalid,
                            target = job.targetMaxGames,
                            priorLinked = priorLinked,
                            // Already polling — do not nest a second
                            // coroutine for the same job.
                            startPolling = false,
                        )
                        if (terminal) return@launch
                    }
                    is ApiResult.HttpError -> {
                        if (result.code == 404) {
                            // Job vanished server-side (janitor swept
                            // it, or owner check failed).  Revert
                            // silently — there's no progress to show
                            // and no error worth surfacing.
                            current = priorLinked
                            onStateChanged?.invoke(priorLinked)
                            return@launch
                        }
                        // 5xx or other — keep polling.  The next tick
                        // may succeed.  No user-visible noise.
                    }
                    is ApiResult.NetworkError, ApiResult.Timeout -> {
                        // Transient; the user may have lost connection
                        // briefly.  Keep polling.
                    }
                }
            }
        }
    }

    private suspend fun performUnlink() {
        val token = requireToken() ?: return
        // Unlink also cancels any server-side job for this player
        // (see ``unlink_account`` in llm/seca/lichess/import_service.py).
        // Stop our poll loop locally so the next tick doesn't surface
        // a transient ``failed`` flash before NotLinked lands.
        importPollJob?.cancel()
        importPollJob = null

        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) { client.unlink(token) }) {
            is ApiResult.Success -> transitionTo(UiState.NotLinked)
            is ApiResult.HttpError -> surfaceHttpError(result.code, "unlink")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    private fun requireToken(): String? {
        val token = authRepository.getToken()
        if (token == null) {
            surfaceErrorKind(ErrorKind.UNAUTHENTICATED)
            return null
        }
        return token
    }

    private fun transitionTo(next: UiState) {
        current = next
        onStateChanged?.invoke(next)
    }

    private fun surfaceErrorKind(kind: ErrorKind) {
        // Revert any in-flight Loading state so the sheet doesn't get
        // stuck on the spinner if onError is the only listener.
        val revertTarget = when (val c = current) {
            is UiState.Loading -> c.previousState
            else -> c
        }
        current = revertTarget
        onStateChanged?.invoke(revertTarget)
        onError?.invoke(kind)
    }

    private fun surfaceHttpError(code: Int, operation: String) {
        val kind = when (operation) {
            "link" -> when (code) {
                400 -> ErrorKind.USERNAME_INVALID
                401 -> ErrorKind.UNAUTHENTICATED
                404 -> ErrorKind.USERNAME_NOT_FOUND
                409 -> ErrorKind.ALREADY_LINKED_TO_OTHER_PLAYER
                502 -> ErrorKind.UPSTREAM
                503 -> ErrorKind.RATE_LIMITED
                else -> ErrorKind.UNKNOWN
            }
            "import" -> when (code) {
                400 -> ErrorKind.NOT_LINKED
                401 -> ErrorKind.UNAUTHENTICATED
                502 -> ErrorKind.UPSTREAM
                503 -> ErrorKind.RATE_LIMITED
                else -> ErrorKind.UNKNOWN
            }
            else -> when (code) {
                401 -> ErrorKind.UNAUTHENTICATED
                502 -> ErrorKind.UPSTREAM
                503 -> ErrorKind.RATE_LIMITED
                else -> ErrorKind.UNKNOWN
            }
        }
        surfaceErrorKind(kind)
    }

    companion object {
        /**
         * Same shape Lichess accepts (mirrored from the backend
         * ``_LICHESS_USERNAME_RE`` and the server-side client guard).
         * Pre-validating client-side gives instant feedback and saves
         * a round-trip for obviously-malformed input.
         */
        private val USERNAME_RE = Regex("^[A-Za-z0-9_-]{2,30}$")

        /**
         * Poll cadence for the v2 import job — 2s.  Server rate limits
         * the GET at 120/min (60/min headroom over the steady-state
         * 30/min this produces).  The Fragment is expected to gate the
         * lifecycle so polling pauses when the app backgrounds; see
         * the bottom-sheet rendering layer.  ``internal`` so tests can
         * read the value to compute deterministic time advances.
         */
        internal const val POLL_INTERVAL_MS: Long = 2_000L

        fun isValidUsername(username: String): Boolean =
            USERNAME_RE.matches(username)
    }
}
