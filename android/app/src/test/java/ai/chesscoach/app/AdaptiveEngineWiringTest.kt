package ai.chesscoach.app

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Verifies that ChessViewModel correctly maps opponentElo → strengthLevel
 * and forwards it to EngineProvider.getBestMove(fen, strengthLevel).
 *
 * Invariants pinned:
 *  1. ELO_600_MAPS_TO_STRENGTH_0:    opponentElo=600  → strengthLevel=0
 *  2. ELO_2400_MAPS_TO_STRENGTH_100: opponentElo=2400 → strengthLevel=100
 *  3. ELO_1500_MAPS_TO_STRENGTH_50:  opponentElo=1500 → strengthLevel=50
 *  4. NO_CACHE_DEFAULTS_TO_100:      null cache → strengthLevel=100 (full strength)
 *  5. API_FAILURE_DEFAULTS_TO_100:   HTTP error from API → strengthLevel=100
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AdaptiveEngineWiringTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = UnconfinedTestDispatcher(scheduler)

    @Before fun setUp() { Dispatchers.setMain(testDispatcher) }
    @After  fun tearDown() { Dispatchers.resetMain() }

    // ── Test doubles ────────────────────────────────────────────────────────────

    private class RecordingEngine : EngineProvider {
        val capturedStrengths = mutableListOf<Int>()
        override fun getBestMove(fen: String): AIMove = AIMove(0, 1, 2, 3)
        override fun getBestMove(fen: String, strengthLevel: Int): AIMove {
            capturedStrengths += strengthLevel
            return AIMove(0, 1, 2, 3)
        }
    }

    private fun fakeApiClient(opponentElo: Int): GameApiClient = object : GameApiClient {
        override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> = ApiResult.HttpError(501)
        override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> = ApiResult.HttpError(501)
        override suspend fun getNextTraining(playerId: String): ApiResult<TrainingRecommendation> = ApiResult.HttpError(501)
        override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> {
            val current = ProgressCurrentDto(
                rating = 1200f, confidence = 0.5f, skillVector = emptyMap(),
                tier = "intermediate", teachingStyle = "intermediate",
                opponentElo = opponentElo, explanationDepth = 0.5f, conceptComplexity = 0.5f,
            )
            return ApiResult.Success(
                PlayerProgressResponse(
                    current, emptyList(),
                    ProgressAnalysisDto(null, 0, emptyMap(), emptyMap(), emptyList()),
                )
            )
        }
    }

    private fun failingApiClient(): GameApiClient = object : GameApiClient {
        override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> = ApiResult.HttpError(501)
        override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> = ApiResult.HttpError(501)
        override suspend fun getNextTraining(playerId: String): ApiResult<TrainingRecommendation> = ApiResult.HttpError(501)
        override suspend fun getPlayerProgress(): ApiResult<PlayerProgressResponse> = ApiResult.HttpError(500)
    }

    private val startFen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"

    private fun playMove(vm: ChessViewModel) {
        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = { startFen },
            applyAIMove = { _, _, _, _ -> '.' },
        )
    }

    // ── 1. ELO_600_MAPS_TO_STRENGTH_0 ──────────────────────────────────────────

    @Test
    fun `opponentElo=600 forwards strengthLevel=0 to engine`() {
        val engine = RecordingEngine()
        val cache = PlayerProfileCache(fakeApiClient(opponentElo = 600))
        val vm = ChessViewModel(engine, testDispatcher, playerProfileCache = cache)
        playMove(vm)
        vm.viewModelScope.cancel()
        assertEquals(0, engine.capturedStrengths.firstOrNull())
    }

    // ── 2. ELO_2400_MAPS_TO_STRENGTH_100 ───────────────────────────────────────

    @Test
    fun `opponentElo=2400 forwards strengthLevel=100 to engine`() {
        val engine = RecordingEngine()
        val cache = PlayerProfileCache(fakeApiClient(opponentElo = 2400))
        val vm = ChessViewModel(engine, testDispatcher, playerProfileCache = cache)
        playMove(vm)
        vm.viewModelScope.cancel()
        assertEquals(100, engine.capturedStrengths.firstOrNull())
    }

    // ── 3. ELO_1500_MAPS_TO_STRENGTH_50 ────────────────────────────────────────

    @Test
    fun `opponentElo=1500 forwards strengthLevel=50 to engine`() {
        val engine = RecordingEngine()
        val cache = PlayerProfileCache(fakeApiClient(opponentElo = 1500))
        val vm = ChessViewModel(engine, testDispatcher, playerProfileCache = cache)
        playMove(vm)
        vm.viewModelScope.cancel()
        assertEquals(50, engine.capturedStrengths.firstOrNull())
    }

    // ── 4. NO_CACHE_DEFAULTS_TO_100 ─────────────────────────────────────────────

    @Test
    fun `null playerProfileCache defaults to strengthLevel=100`() {
        val engine = RecordingEngine()
        val vm = ChessViewModel(engine, testDispatcher, playerProfileCache = null)
        playMove(vm)
        vm.viewModelScope.cancel()
        assertEquals(100, engine.capturedStrengths.firstOrNull())
    }

    // ── 5. API_FAILURE_DEFAULTS_TO_100 ──────────────────────────────────────────

    @Test
    fun `API failure falls back to strengthLevel=100`() {
        val engine = RecordingEngine()
        val cache = PlayerProfileCache(failingApiClient())
        val vm = ChessViewModel(engine, testDispatcher, playerProfileCache = cache)
        playMove(vm)
        vm.viewModelScope.cancel()
        assertEquals(100, engine.capturedStrengths.firstOrNull())
    }
}
