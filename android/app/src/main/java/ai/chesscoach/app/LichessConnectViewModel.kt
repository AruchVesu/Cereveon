package ai.chesscoach.app

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
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

    // ----------------------------------------------------------------------
    // Operation handlers
    // ----------------------------------------------------------------------

    private suspend fun performStatus() {
        val token = requireToken() ?: return
        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) { client.status(token) }) {
            is ApiResult.Success -> {
                val s = result.data
                transitionTo(
                    if (s.linked) {
                        UiState.Linked(
                            username = s.externalUsername ?: "",
                            linkedAt = s.linkedAt,
                            lastImportedAt = s.lastImportedAt,
                            importedGameCount = s.importedGameCount,
                        )
                    } else {
                        UiState.NotLinked
                    }
                )
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
        transitionTo(UiState.Loading(previousState = current))
        when (val result = withContext(ioDispatcher) {
            client.importGames(token = token, maxGames = maxGames)
        }) {
            is ApiResult.Success -> {
                val s = result.data
                transitionTo(
                    priorLinked.copy(
                        importedGameCount = priorLinked.importedGameCount + s.inserted,
                        lastImportedAt = s.lastImportedAt ?: priorLinked.lastImportedAt,
                        // Surface the latest import counts so the UI can
                        // render "Imported N (skipped M duplicates)" once.
                        lastImportSummary = s,
                        // Clear the one-shot calibration banner now that
                        // the user has moved on from the link transition.
                        calibration = null,
                    )
                )
            }
            is ApiResult.HttpError -> surfaceHttpError(result.code, "import")
            is ApiResult.NetworkError -> surfaceErrorKind(ErrorKind.NETWORK)
            ApiResult.Timeout -> surfaceErrorKind(ErrorKind.TIMEOUT)
        }
    }

    private suspend fun performUnlink() {
        val token = requireToken() ?: return
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

        fun isValidUsername(username: String): Boolean =
            USERNAME_RE.matches(username)
    }
}
