package ai.chesscoach.app

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class PlayerProfileCache(private val apiClient: GameApiClient) {

    private val mutex = Mutex()
    @Volatile private var cached: ProgressCurrentDto? = null

    suspend fun getOpponentElo(): Int {
        cached?.let { return it.opponentElo }
        return mutex.withLock {
            cached?.opponentElo ?: run {
                val result = apiClient.getPlayerProgress()
                if (result is ApiResult.Success) {
                    cached = result.data.current
                    result.data.current.opponentElo
                } else {
                    error("getPlayerProgress failed: $result")
                }
            }
        }
    }

    fun invalidate() {
        cached = null
    }
}
