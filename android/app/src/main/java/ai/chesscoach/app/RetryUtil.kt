package ai.chesscoach.app

import kotlinx.coroutines.delay

/**
 * Retries [block] up to [maxAttempts] times with exponential backoff + jitter.
 *
 * Only retries on transient transport failures ([ApiResult.Timeout] and
 * [ApiResult.NetworkError]). Definitive server responses ([ApiResult.Success]
 * and [ApiResult.HttpError]) are returned immediately — retrying a 4xx/5xx
 * would not produce a different outcome and wastes battery on mobile.
 *
 * @param maxAttempts    Total number of attempts including the first.
 * @param initialDelayMs Pause before the second attempt in milliseconds.
 * @param maxDelayMs     Upper cap on the delay between attempts.
 */
suspend fun <T> withRetry(
    maxAttempts: Int = 3,
    initialDelayMs: Long = 500L,
    maxDelayMs: Long = 5_000L,
    block: suspend () -> ApiResult<T>,
): ApiResult<T> {
    var delayMs = initialDelayMs
    repeat(maxAttempts - 1) {
        val result = block()
        // Non-local return from the inline lambda — exits withRetry immediately.
        if (result !is ApiResult.Timeout && result !is ApiResult.NetworkError) return result
        delay(delayMs + (0L..100L).random())          // jitter avoids thundering-herd
        delayMs = (delayMs * 2).coerceAtMost(maxDelayMs)
    }
    return block()
}
