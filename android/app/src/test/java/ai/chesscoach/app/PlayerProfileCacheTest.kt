package ai.chesscoach.app

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class PlayerProfileCacheTest {

    private fun fakeClient(elo: Int) = object : GameApiClient {
        var callCount = 0
        override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> = ApiResult.HttpError(501)
        override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> = ApiResult.HttpError(501)
        override suspend fun getNextTraining(playerId: String): ApiResult<TrainingRecommendation> = ApiResult.HttpError(501)
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

    @Test
    fun `returns opponentElo from API`() = runTest {
        val client = fakeClient(elo = 1800)
        val cache = PlayerProfileCache(client)
        assertEquals(1800, cache.getOpponentElo())
    }

    @Test
    fun `caches result — API called only once`() = runTest {
        val client = fakeClient(elo = 1400)
        val cache = PlayerProfileCache(client)
        cache.getOpponentElo()
        cache.getOpponentElo()
        assertEquals(1, client.callCount)
    }

    @Test
    fun `invalidate forces re-fetch`() = runTest {
        val client = fakeClient(elo = 1000)
        val cache = PlayerProfileCache(client)
        cache.getOpponentElo()
        cache.invalidate()
        cache.getOpponentElo()
        assertEquals(2, client.callCount)
    }

    @Test(expected = IllegalStateException::class)
    fun `throws IllegalStateException when API fails`() = runTest {
        val errorClient = object : GameApiClient {
            override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> = ApiResult.HttpError(501)
            override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> = ApiResult.HttpError(501)
            override suspend fun getNextTraining(playerId: String): ApiResult<TrainingRecommendation> = ApiResult.HttpError(501)
            override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> = ApiResult.HttpError(500)
        }
        val cache = PlayerProfileCache(errorClient)
        cache.getOpponentElo()
    }
}
