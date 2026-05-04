package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-Kotlin tests for [PendingGameFinish].
 *
 * Invariants pinned
 * -----------------
 *  1. isTransient retries timeouts, network errors, and 5xx HTTP.
 *  2. isTransient does NOT retry 4xx HTTP or successes.
 *  3. toJson + fromJson roundtrip preserves every field including
 *     null playerId / gameId.
 *  4. fromJson returns null (rather than crashing) on malformed JSON
 *     so a corrupt prefs blob can't take down MainActivity onCreate.
 *  5. Empty weaknesses map roundtrips as an empty map (not null).
 */
class PendingGameFinishTest {

    // ── isTransient ──────────────────────────────────────────────────

    @Test
    fun `timeout is transient`() {
        assertTrue(PendingGameFinish.isTransient(ApiResult.Timeout))
    }

    @Test
    fun `network error is transient`() {
        assertTrue(PendingGameFinish.isTransient(ApiResult.NetworkError(RuntimeException("boom"))))
    }

    @Test
    fun `5xx http errors are transient`() {
        for (code in listOf(500, 502, 503, 504, 599)) {
            assertTrue(
                "HTTP $code must be retryable — server-side incidents go away on their own",
                PendingGameFinish.isTransient(ApiResult.HttpError(code)),
            )
        }
    }

    @Test
    fun `4xx http errors are NOT transient`() {
        // 401 is handled separately by handleSessionExpired — not the
        // retry-loop's job.  All other 4xx mean "the server actively
        // rejected this payload"; retrying with the same payload
        // would just fail again, so we don't.
        for (code in listOf(400, 401, 403, 404, 409, 422, 429)) {
            assertFalse(
                "HTTP $code must NOT be retried — the server rejected the payload, " +
                    "retrying would just fail again",
                PendingGameFinish.isTransient(ApiResult.HttpError(code)),
            )
        }
    }

    @Test
    fun `success is not retried`() {
        assertFalse(PendingGameFinish.isTransient(ApiResult.Success("ok")))
    }

    // ── toJson + fromJson roundtrip ──────────────────────────────────

    @Test
    fun `roundtrip preserves all fields`() {
        val req = GameFinishRequest(
            pgn = "[Event \"x\"]\n[White \"a\"]\n[Black \"b\"]\n[Result \"*\"]\n\n1. e4 e5 2. Nf3 Nc6 *",
            result = "win",
            accuracy = 0.85f,
            weaknesses = mapOf("endgame" to 0.6f, "tactics" to 0.4f),
            playerId = "player-abc",
            gameId = "game-xyz-123",
        )
        val parsed = PendingGameFinish.fromJson(PendingGameFinish.toJson(req))
        assertNotNull("roundtrip must not return null", parsed)
        parsed!!
        assertEquals(req.pgn, parsed.pgn)
        assertEquals(req.result, parsed.result)
        assertEquals(req.accuracy, parsed.accuracy)
        assertEquals(req.weaknesses, parsed.weaknesses)
        assertEquals(req.playerId, parsed.playerId)
        assertEquals(req.gameId, parsed.gameId)
    }

    @Test
    fun `roundtrip preserves null optional fields`() {
        val req = GameFinishRequest(
            pgn = "[Event \"x\"]\n[White \"a\"]\n[Black \"b\"]\n[Result \"*\"]\n\n1. e4 e5 *",
            result = "draw",
            accuracy = 0.5f,
            weaknesses = mapOf(),
            playerId = null,
            gameId = null,
        )
        val parsed = PendingGameFinish.fromJson(PendingGameFinish.toJson(req))!!
        assertNull("playerId must round-trip as null when absent",  parsed.playerId)
        assertNull("gameId must round-trip as null when absent",    parsed.gameId)
    }

    @Test
    fun `roundtrip preserves empty weaknesses as empty map`() {
        val req = GameFinishRequest(
            pgn = "[Event \"x\"]\n[White \"a\"]\n[Black \"b\"]\n[Result \"*\"]\n\n1. e4 e5 *",
            result = "loss",
            accuracy = 0.1f,
            weaknesses = emptyMap(),
        )
        val parsed = PendingGameFinish.fromJson(PendingGameFinish.toJson(req))!!
        assertEquals(emptyMap<String, Float>(), parsed.weaknesses)
    }

    // ── fromJson defensive against corruption ────────────────────────

    @Test
    fun `fromJson returns null for malformed JSON`() {
        // SharedPreferences corruption / partial write / schema drift
        // across app upgrades — never crash MainActivity.onCreate.
        for (junk in listOf("", "{", "{\"pgn\":}", "not-json-at-all", "null", "[]")) {
            assertNull(
                "malformed input '${junk.take(20)}' was not handled gracefully",
                PendingGameFinish.fromJson(junk),
            )
        }
    }

    @Test
    fun `fromJson returns null for missing required fields`() {
        // Missing required fields (pgn, result, accuracy) → drop the
        // slot rather than panic.
        assertNull(PendingGameFinish.fromJson("""{"result":"win","accuracy":0.5}"""))
        assertNull(PendingGameFinish.fromJson("""{"pgn":"x","accuracy":0.5}"""))
        assertNull(PendingGameFinish.fromJson("""{"pgn":"x","result":"win"}"""))
    }

    // ── classifyRetryResult ──────────────────────────────────────────

    @Test
    fun `classifyRetryResult maps Success to DONE`() {
        assertEquals(
            PendingGameFinish.RetryAction.DONE,
            PendingGameFinish.classifyRetryResult(ApiResult.Success("ok")),
        )
    }

    @Test
    fun `classifyRetryResult maps 401 to SESSION_EXPIRED`() {
        // 401 is special-cased so retry callers can route to login
        // and keep the payload for after re-auth.
        assertEquals(
            PendingGameFinish.RetryAction.SESSION_EXPIRED,
            PendingGameFinish.classifyRetryResult(ApiResult.HttpError(401)),
        )
    }

    @Test
    fun `classifyRetryResult maps 5xx to RESTORE`() {
        // Server-side incident; payload stays put for next try.
        for (code in listOf(500, 502, 503, 504)) {
            assertEquals(
                "HTTP $code must be RESTORE (transient → keep slot)",
                PendingGameFinish.RetryAction.RESTORE,
                PendingGameFinish.classifyRetryResult(ApiResult.HttpError(code)),
            )
        }
    }

    @Test
    fun `classifyRetryResult maps other 4xx to DROP`() {
        // Server actively rejected the payload; retrying same payload
        // would just fail again.  Drop the slot so we don't keep
        // tripping over it.
        for (code in listOf(400, 403, 404, 409, 422, 429)) {
            assertEquals(
                "HTTP $code must be DROP (non-retryable)",
                PendingGameFinish.RetryAction.DROP,
                PendingGameFinish.classifyRetryResult(ApiResult.HttpError(code)),
            )
        }
    }

    @Test
    fun `classifyRetryResult maps NetworkError and Timeout to RESTORE`() {
        assertEquals(
            PendingGameFinish.RetryAction.RESTORE,
            PendingGameFinish.classifyRetryResult(ApiResult.NetworkError(RuntimeException("dns"))),
        )
        assertEquals(
            PendingGameFinish.RetryAction.RESTORE,
            PendingGameFinish.classifyRetryResult(ApiResult.Timeout),
        )
    }
}
