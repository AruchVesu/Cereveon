package ai.chesscoach.app

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Discriminated state for the backend's SECA safe-mode flag.
 *
 *  - [Unknown]  No status check has completed yet (cold-start before
 *               the first refresh, or all previous refreshes failed
 *               before any successful response).  Treated as "not yet
 *               cleared for coaching" — the gate fails closed.
 *  - [Safe]     The most recent `GET /seca/status` returned
 *               `safe_mode: true`.  Coaching requests permitted.
 *  - [Unsafe]   Either the backend reported `safe_mode: false`, or the
 *               status check itself failed (HTTP error / timeout /
 *               network unreachable).  Coaching requests refused; the
 *               UI surfaces [reason] so the user can see why.
 */
sealed class SecaSafetyState {
    object Unknown : SecaSafetyState()
    object Safe : SecaSafetyState()
    data class Unsafe(val reason: String) : SecaSafetyState()
}

/**
 * Cold-start (and on-resume) gate that decides whether the Android
 * client may send coaching requests to the backend.
 *
 * The README contract says: the client must confirm `safe_mode: true`
 * via `GET /seca/status` before sending coaching requests.  This
 * interface is the local representation of that contract.
 *
 * Why a fail-closed default
 * -------------------------
 * The state starts at [SecaSafetyState.Unknown] and stays there until
 * the first successful refresh.  [isSafe] returns false in that
 * window, so a coaching call that races the cold-start check sees the
 * "not yet cleared" state and is refused — never the optimistic
 * "assume safe until proven otherwise" path that would let an unsafe
 * backend slip a single coaching request through during the initial
 * round-trip.
 *
 * Why network errors map to Unsafe
 * --------------------------------
 * If `/seca/status` is unreachable, the client cannot prove the
 * backend is safe.  The conservative reading of "before sending
 * coaching requests, confirm safe_mode" is "no proof, no coaching".
 * The user sees the failure reason and can retry.  Reverse
 * interpretation ("network broken, assume safe") would mask a
 * deliberately disabled `/seca/status` endpoint — exactly the threat
 * we're guarding against.
 */
interface SecaSafetyGate {
    val state: StateFlow<SecaSafetyState>

    /** Re-fetch `GET /seca/status` and update [state]. Idempotent. */
    suspend fun refresh()

    /** True when [state] is [SecaSafetyState.Safe]. */
    fun isSafe(): Boolean = state.value is SecaSafetyState.Safe
}

/**
 * Default gate backed by a [GameApiClient].  Holds a [MutableStateFlow]
 * so observers in the activity can render the banner reactively
 * without polling.
 */
class HttpSecaSafetyGate(
    private val client: GameApiClient,
) : SecaSafetyGate {

    private val _state = MutableStateFlow<SecaSafetyState>(SecaSafetyState.Unknown)
    override val state: StateFlow<SecaSafetyState> = _state.asStateFlow()

    override suspend fun refresh() {
        _state.value =
            when (val r = client.getSecaStatus()) {
                is ApiResult.Success ->
                    if (r.data.safeModeEnabled) {
                        SecaSafetyState.Safe
                    } else {
                        SecaSafetyState.Unsafe(
                            "Backend reports safe_mode=false (adaptive learning may be active)",
                        )
                    }
                is ApiResult.HttpError ->
                    SecaSafetyState.Unsafe("Status check failed (HTTP ${r.code})")
                is ApiResult.Timeout ->
                    SecaSafetyState.Unsafe("Status check timed out")
                is ApiResult.NetworkError ->
                    SecaSafetyState.Unsafe("Status check unreachable")
            }
    }
}
