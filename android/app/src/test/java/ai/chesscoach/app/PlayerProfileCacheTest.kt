package ai.chesscoach.app

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [PlayerProfileCache] contract tests.
 *
 * Stable test IDs (do NOT rename):
 *   PPC_01  Returns opponentElo from the API on first read
 *   PPC_02  Within-TTL repeat read uses the cache (no second API call)
 *   PPC_03  invalidate() clears the cache → next read re-fetches
 *   PPC_04  TTL expiry triggers a re-fetch automatically
 *   PPC_05  Re-fetch resets the expiry window (next read within new
 *           TTL is a cache hit, not a third API call)
 *   PPC_06  Custom TTL respected (nowMs + ttlMillis composition)
 *   PPC_07  IllegalStateException on API failure (preserved
 *           backward-compat with the original cache contract)
 *
 * The clock is injected via the optional ``nowMs`` constructor
 * parameter; tests advance it explicitly so expiry is deterministic
 * without sleeping or relying on wall-clock time.
 */
class PlayerProfileCacheTest {

    private class FakeClient(private val elo: Int = 1500) : GameApiClient {
        var callCount: Int = 0
            private set

        override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> =
            ApiResult.HttpError(501)

        override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> =
            ApiResult.HttpError(501)

        override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> {
            callCount++
            val current = ProgressCurrentDto(
                rating = 1500f, confidence = 0.7f, skillVector = emptyMap(),
                tier = "intermediate", teachingStyle = "intermediate",
                opponentElo = elo, explanationDepth = 0.5f, conceptComplexity = 0.5f,
            )
            return ApiResult.Success(
                PlayerProgressResponse(
                    current = current,
                    history = emptyList(),
                    analysis = ProgressAnalysisDto(
                        dominantCategory = null, gamesAnalyzed = 0,
                        categoryScores = emptyMap(), phaseRates = emptyMap(),
                        recommendations = emptyList(),
                    ),
                )
            )
        }
    }

    /**
     * Mutable clock holder.  Tests bump ``now`` to simulate the passage
     * of time without sleeping.  Reads from the cache observe the
     * latest value because the cache calls the lambda each access.
     */
    private class FakeClock(initial: Long = 0L) {
        var now: Long = initial
        val asLambda: () -> Long = { now }
    }

    @Test
    fun `PPC_01 returns opponentElo from API`() = runTest {
        val client = FakeClient(elo = 1800)
        val cache = PlayerProfileCache(client)
        assertEquals(1800, cache.getOpponentElo())
        assertEquals(1, client.callCount)
    }

    @Test
    fun `PPC_02 within-TTL repeat read uses cache`() = runTest {
        val client = FakeClient(elo = 1400)
        val clock = FakeClock(initial = 0L)
        val cache = PlayerProfileCache(client, ttlMillis = 60_000L, nowMs = clock.asLambda)

        cache.getOpponentElo()
        // Half the TTL elapses — well within the cache window.
        clock.now = 30_000L
        cache.getOpponentElo()

        assertEquals(
            "Within-TTL second read must reuse the cache; expected 1 API call, got ${client.callCount}",
            1, client.callCount,
        )
    }

    @Test
    fun `PPC_03 invalidate forces re-fetch`() = runTest {
        val client = FakeClient(elo = 1000)
        val cache = PlayerProfileCache(client)
        cache.getOpponentElo()
        cache.invalidate()
        cache.getOpponentElo()
        assertEquals(2, client.callCount)
    }

    @Test
    fun `PPC_04 TTL expiry triggers re-fetch`() = runTest {
        val client = FakeClient(elo = 1600)
        val clock = FakeClock(initial = 0L)
        val cache = PlayerProfileCache(client, ttlMillis = 60_000L, nowMs = clock.asLambda)

        cache.getOpponentElo()
        // Advance past the TTL boundary.  The next read must re-fetch
        // automatically — without this, a backend rating bump from a
        // sibling account, admin edit, or manual /auth/me push wouldn't
        // surface until the next /game/finish, which may be hours away.
        clock.now = 60_001L
        cache.getOpponentElo()

        assertEquals(
            "TTL-expired read must re-fetch; expected 2 API calls, got ${client.callCount}",
            2, client.callCount,
        )
    }

    @Test
    fun `PPC_05 re-fetch resets the expiry window`() = runTest {
        val client = FakeClient(elo = 1500)
        val clock = FakeClock(initial = 0L)
        val cache = PlayerProfileCache(client, ttlMillis = 60_000L, nowMs = clock.asLambda)

        cache.getOpponentElo()       // call 1, expires at 60_000
        clock.now = 60_001L
        cache.getOpponentElo()       // call 2, expires at 120_001
        clock.now = 90_000L          // within the new window
        cache.getOpponentElo()       // cached — no third call

        assertEquals(
            "After a re-fetch the window resets; expected 2 calls total, got ${client.callCount}",
            2, client.callCount,
        )
    }

    @Test
    fun `PPC_06 custom TTL respected`() = runTest {
        val client = FakeClient(elo = 1200)
        val clock = FakeClock(initial = 0L)
        // Tighter 1-second TTL — exercises that the param actually
        // composes with the clock.
        val cache = PlayerProfileCache(client, ttlMillis = 1_000L, nowMs = clock.asLambda)

        cache.getOpponentElo()
        clock.now = 999L
        cache.getOpponentElo()       // still inside 1-second window
        clock.now = 1_001L
        cache.getOpponentElo()       // expired

        assertEquals(
            "Custom TTL must be honoured; expected 2 API calls, got ${client.callCount}",
            2, client.callCount,
        )
    }

    @Test(expected = IllegalStateException::class)
    fun `PPC_07 throws IllegalStateException when API fails`() = runTest {
        val errorClient = object : GameApiClient {
            override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> =
                ApiResult.HttpError(501)

            override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> =
                ApiResult.HttpError(501)

            override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> =
                ApiResult.HttpError(500)
        }
        val cache = PlayerProfileCache(errorClient)
        cache.getOpponentElo()
    }
}
