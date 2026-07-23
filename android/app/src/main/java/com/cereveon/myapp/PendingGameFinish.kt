package com.cereveon.myapp

import android.content.SharedPreferences
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString

/**
 * Helpers for the offline /game/finish retry path.
 *
 * Lifecycle
 * ---------
 * When MainActivity.onGameOver hits a transient failure on
 * /game/finish — timeout, network error, or 5xx — the request payload
 * is JSON-serialised via [toJson] and persisted to
 * [PREF_PENDING_FINISH_PAYLOAD] in SharedPreferences.  On the next
 * MainActivity cold-start [fromJson] rehydrates it and the activity
 * tries the call again.  Success clears the slot; a still-transient
 * failure (including 429 — see [isTransient]) leaves it for the next
 * attempt; any other 4xx response (except 401, which is handled
 * separately) clears the slot since the call would just fail again.
 *
 * This closes a real silent-data-loss bug: an entire game's PGN +
 * weakness analysis used to be dropped on the floor when the network
 * hiccupped at exactly the wrong moment, and the user had no way to
 * recover the work.
 *
 * The slot is one-deep on purpose — chess games take 10–30 minutes,
 * so multiple pending finishes is a vanishingly rare edge.  A second
 * pending finish overwrites the first; the older one is gone.  Future
 * improvement: a queue, or fire-and-forget retry that doesn't block
 * the next finish attempt.
 */
object PendingGameFinish {

    const val PREF_PENDING_FINISH_PAYLOAD = "pending_game_finish_payload"

    /**
     * Should this [ApiResult] failure be retried later, or is it
     * permanent (4xx, success)?  We retry only on signals that
     * suggest "the request never reached a healthy server":
     *   - Timeout: server might be slow / unreachable, retry
     *   - NetworkError: connection refused / DNS / etc., retry
     *   - HttpError 5xx: server-side incident, retry
     *   - HttpError 429: rate-limited, NOT a payload rejection — the
     *     same payload succeeds once the window resets.  /game/finish
     *     is limited to 10/min server-side, so a burst of quick games
     *     (or a proxy-collapsed rate bucket) must not cost the game.
     *
     * Other 4xx (401 is handled by handleSessionExpired upstream)
     * indicates a payload the server actively rejected — retrying
     * with the same payload would just fail again, so we don't.
     */
    fun isTransient(result: ApiResult<*>): Boolean = when (result) {
        is ApiResult.Timeout       -> true
        is ApiResult.NetworkError  -> true
        is ApiResult.HttpError     -> result.code >= 500 || result.code == 429
        is ApiResult.Success       -> false
    }

    /**
     * Serialise a GameFinishRequest into a JSON blob suitable for
     * SharedPreferences.  Schema mirrors the wire format the
     * HttpGameApiClient sends so the round-trip is loss-free.  Since
     * Sprint 4.3.C, [GameFinishRequest] itself is @Serializable so
     * the on-disk format is identical to what the HTTP client posts.
     * Null ``player_id`` / ``game_id`` are dropped by the shared
     * [ApiJson] config (``encodeDefaults = false``).
     */
    fun toJson(req: GameFinishRequest): String =
        ApiJson.encodeToString(req)

    /**
     * Inverse of [toJson] — returns null when the blob is malformed
     * (corrupted prefs, partial write, schema drift across upgrades)
     * so the caller can drop the slot and continue rather than crash.
     * Missing optional fields (player_id, game_id) round-trip to null.
     * An empty-string optional from a legacy payload is normalised
     * back to null to preserve the pre-Sprint-4.3.C behaviour.
     */
    fun fromJson(json: String): GameFinishRequest? = try {
        val req = ApiJson.decodeFromString<GameFinishRequest>(json)
        // Legacy parser dropped empty optionals to null via ``takeIf``;
        // keep that invariant so a fromJson(toJson(req)) round-trip
        // still maps "" → null for callers that key off nullability.
        req.copy(
            playerId = req.playerId?.takeIf { it.isNotEmpty() },
            gameId = req.gameId?.takeIf { it.isNotEmpty() },
        )
    } catch (_: Exception) {
        null
    }

    /**
     * What to do with the prefs slot after a retry attempt completes.
     * Pulled out as a pure mapping over [ApiResult] so it can be unit
     * tested on the host JVM without spinning up SharedPreferences.
     */
    enum class RetryAction {
        /** /game/finish succeeded — drop the slot. */
        DONE,
        /** Non-transient failure (e.g. 4xx) — drop the slot since
         *  retrying with the same payload would just fail again. */
        DROP,
        /** Transient failure (timeout / network / 5xx) — keep the
         *  slot for the next cold-start. */
        RESTORE,
        /** 401 — keep the slot, route the caller to its login flow.
         *  The next successful auth + cold-start will retry. */
        SESSION_EXPIRED,
    }

    /**
     * Classify the outcome of the retry's /game/finish call.  Mirrors
     * [isTransient] but adds the 401-vs-other-4xx distinction since
     * retry semantics differ:
     *   - 401 means "auth lapsed", which is recoverable on next login;
     *     keep the payload.
     *   - 429 is transient per [isTransient]; keep the payload.
     *   - Other 4xx means "server rejected this payload"; drop it.
     */
    fun classifyRetryResult(result: ApiResult<*>): RetryAction = when (result) {
        is ApiResult.Success      -> RetryAction.DONE
        is ApiResult.HttpError    -> when {
            result.code == 401 -> RetryAction.SESSION_EXPIRED
            isTransient(result) -> RetryAction.RESTORE
            else                -> RetryAction.DROP
        }
        is ApiResult.NetworkError -> RetryAction.RESTORE
        is ApiResult.Timeout      -> RetryAction.RESTORE
    }

    /**
     * Drop the pending finish payload from [prefs].  Idempotent —
     * a no-op when no payload is present.  Returns true iff a
     * payload was actually removed (so callers can decide whether
     * to show a "Discarded" toast vs. nothing).
     *
     * Used by HomeActivity's long-press-on-sync-indicator → confirm
     * affordance; gives the user an escape hatch when a stuck pending
     * finish keeps failing (e.g. the underlying game has a malformed
     * PGN the server perma-rejects but isTransient classifies as 5xx,
     * or the user just no longer cares about the lost game).
     */
    fun discardFromPrefs(prefs: SharedPreferences): Boolean {
        if (!prefs.contains(PREF_PENDING_FINISH_PAYLOAD)) return false
        prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
        return true
    }

    /**
     * Process-singleton guard against double-fire when both
     * MainActivity and HomeActivity try to retry on the same
     * cold-start.  Without this, the user's offline game would
     * post twice and create two GameEvent rows server-side.
     *
     * Compare-and-set: a second concurrent caller sees the flag
     * already set and returns early.  Cleared in the launch's
     * `finally` so a thrown / cancelled coroutine doesn't pin the
     * flag forever.
     */
    private val retryInFlight = AtomicBoolean(false)

    /**
     * Read the pending payload from [prefs], fire [client]'s
     * /game/finish, and update the slot per [classifyRetryResult].
     *
     * Designed to be safe to call from multiple activity cold-starts
     * back-to-back — the [retryInFlight] flag prevents double-firing.
     * Both [onSuccess] and [onSessionExpired] are invoked on the
     * coroutine's resumed dispatcher (typically Main, since the
     * caller passes [androidx.lifecycle.lifecycleScope]).
     */
    fun retryFromPrefs(
        prefs: SharedPreferences,
        client: GameApiClient,
        scope: CoroutineScope,
        onSuccess: () -> Unit = {},
        onSessionExpired: () -> Unit = {},
    ) {
        if (!retryInFlight.compareAndSet(false, true)) {
            Log.d("GAME", "Pending finish retry skipped — another retry is in flight")
            return
        }
        val raw = prefs.getString(PREF_PENDING_FINISH_PAYLOAD, null)
        if (raw == null) {
            retryInFlight.set(false)
            return
        }
        val req = fromJson(raw)
        if (req == null) {
            // Corrupted blob — drop it so we don't keep tripping over it.
            prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
            retryInFlight.set(false)
            Log.w("GAME", "Dropping malformed pending finish payload")
            return
        }
        scope.launch {
            try {
                when (classifyRetryResult(client.finishGame(req))) {
                    RetryAction.DONE -> {
                        prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
                        onSuccess()
                        Log.d("GAME", "Pending finish synced successfully")
                    }
                    RetryAction.DROP -> {
                        prefs.edit().remove(PREF_PENDING_FINISH_PAYLOAD).apply()
                        Log.w("GAME", "Pending finish non-retryable; dropping")
                    }
                    RetryAction.RESTORE -> {
                        Log.d("GAME", "Pending finish still transient; keeping for next try")
                    }
                    RetryAction.SESSION_EXPIRED -> {
                        Log.d("GAME", "Pending finish hit 401; keeping for post-login retry")
                        onSessionExpired()
                    }
                }
            } finally {
                retryInFlight.set(false)
            }
        }
    }
}
