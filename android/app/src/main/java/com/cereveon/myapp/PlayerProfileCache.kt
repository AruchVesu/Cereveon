package com.cereveon.myapp

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * In-memory cache for the authenticated player's progress snapshot.
 *
 * The cache exists so the per-move strength dial (`/move` request)
 * doesn't hit `/player/progress` on every move — the snapshot only
 * really changes between games.  Two invalidation paths:
 *
 *  - **Explicit**: [invalidate] is called from
 *    `MainActivity` after every successful `/game/finish` so the next
 *    AI move sees the rating bump the server applied.
 *  - **Implicit (TTL)**: the cached entry expires after [ttlMillis]
 *    (default 5 minutes).  This bounds the staleness window for
 *    out-of-band changes the explicit path can't see — admin edits to
 *    the player's rating, a sibling account on another device finishing
 *    a game, a manual `/auth/me` push that updated the cached profile.
 *    Mid-session drift is rare but not impossible; without a TTL the
 *    only recovery is killing and reopening the app.
 *
 * Five minutes is a comfortable default: long enough that the typical
 * within-game read is a cache hit, short enough that drift recovers
 * automatically before the next reasonable game starts.
 *
 * The clock is injectable for tests via [nowMs] so we can exercise
 * expiry deterministically without manipulating wall-clock time.
 */
class PlayerProfileCache(
    private val apiClient: GameApiClient,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS,
    private val nowMs: () -> Long = System::currentTimeMillis,
) {

    private data class CachedEntry(
        val dto: ProgressCurrentDto,
        val expiresAtMillis: Long,
    )

    private val mutex = Mutex()

    @Volatile private var cached: CachedEntry? = null

    suspend fun getOpponentElo(): Int {
        // Fast path — read the volatile entry once and validate its
        // freshness against the injected clock.  Two reads of `cached`
        // can in principle return different snapshots if a writer
        // races; that's harmless because both are valid points in
        // time and the slow path under the mutex resolves any tie.
        cached?.let { entry ->
            if (entry.expiresAtMillis > nowMs()) {
                return entry.dto.opponentElo
            }
        }

        return mutex.withLock {
            // Re-check under the lock — a concurrent waiter may have
            // already refreshed by the time we acquired it.
            cached?.let { entry ->
                if (entry.expiresAtMillis > nowMs()) {
                    return@withLock entry.dto.opponentElo
                }
            }
            val result = apiClient.getPlayerProgress()
            if (result is ApiResult.Success) {
                val current = result.data.current
                cached = CachedEntry(
                    dto = current,
                    expiresAtMillis = nowMs() + ttlMillis,
                )
                current.opponentElo
            } else {
                error("getPlayerProgress failed: $result")
            }
        }
    }

    /**
     * Non-suspending peek at the cached opponent ELO.  Returns null
     * when the cache is empty or the cached entry has expired.
     *
     * Exists so the AI-move dispatch path ([ChessViewModel.requestAIMove])
     * never blocks on `/player/progress`.  When the backend is slow
     * (or unreachable — see SECA "Backend safety unverified" condition)
     * the suspending [getOpponentElo] would otherwise wait up to the
     * read timeout (~15s) on every move, which leaves `turn = AI` and
     * makes the board appear frozen until the request finally fails.
     *
     * Callers that read this should fall back to a deterministic
     * default (e.g. strength 100) when null is returned, and rely on
     * a background [warm] coroutine to populate the cache out-of-band.
     */
    fun cachedOpponentEloOrNull(): Int? =
        cached?.takeIf { it.expiresAtMillis > nowMs() }?.dto?.opponentElo

    /**
     * Best-effort cache populate.  Suitable for fire-and-forget use
     * from `lifecycleScope.launch { cache.warm() }` at cold-start and
     * after [invalidate] (e.g. post-`/game/finish`).  Failures are
     * swallowed — the AI path tolerates a null cache via
     * [cachedOpponentEloOrNull].
     */
    suspend fun warm() {
        try {
            getOpponentElo()
        } catch (_: Exception) {
            // The backend is unhealthy; the AI path falls back to
            // strength 100 in the meantime.  No need to surface the
            // failure here — the SECA snackbar already explains.
        }
    }

    fun invalidate() {
        cached = null
    }

    companion object {
        /** Default time-to-live for cached progress: 5 minutes. */
        const val DEFAULT_TTL_MILLIS: Long = 5L * 60L * 1000L
    }
}
