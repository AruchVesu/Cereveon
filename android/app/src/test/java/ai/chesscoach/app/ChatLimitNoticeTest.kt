package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Parser table for [ChatLimitNotice] — the client-side reading of the
 * entitlements 402 body on POST /chat and /chat/stream
 * (API_CONTRACTS.md §5 "Errors").
 *
 * The stream client surfaces non-200s as
 * `StreamChunk.StreamError("HTTP <code>: <body>")`, so
 * [ChatLimitNotice.fromStreamErrorMessage] must extract and parse the
 * JSON suffix — and must return null for EVERYTHING that isn't the
 * quota contract, because ChatBottomSheet probes every stream error
 * with it (a false positive would hijack unrelated failures into the
 * paywall).
 */
class ChatLimitNoticeTest {

    private val quotaBody =
        """{"error": "chat_daily_limit", "plan": "free", "limit": 3, "used": 3, """ +
            """"upgrade": {"product": "pro_monthly"}}"""

    // ── fromBody ─────────────────────────────────────────────────────

    @Test
    fun `fromBody parses the documented contract`() {
        val notice = ChatLimitNotice.fromBody(quotaBody)
        assertEquals("chat_daily_limit", notice?.error)
        assertEquals("free", notice?.plan)
        assertEquals(3, notice?.limit)
        assertEquals(3, notice?.used)
    }

    @Test
    fun `fromBody parses reset_at when present`() {
        // Rolling-24h window: the server names the unlock instant so the
        // client can count down to it (API_CONTRACTS.md §5).
        val withReset =
            """{"error": "chat_daily_limit", "plan": "free", "limit": 3, "used": 3, """ +
                """"reset_at": "2026-07-23T11:59:00", "upgrade": {"product": "pro_monthly"}}"""
        assertEquals("2026-07-23T11:59:00", ChatLimitNotice.fromBody(withReset)?.resetAt)
    }

    @Test
    fun `fromBody leaves reset_at null when the server omits it`() {
        // Older server / calendar path → field absent → null; the client
        // falls back to the UTC-midnight countdown (DailyLimitReset).
        assertNull(ChatLimitNotice.fromBody(quotaBody)?.resetAt)
    }

    @Test
    fun `fromBody ignores unknown keys like upgrade`() {
        // upgrade.product is advisory; the parser must tolerate it (and
        // any future additive keys) via ignoreUnknownKeys.
        assertEquals("free", ChatLimitNotice.fromBody(quotaBody)?.plan)
    }

    @Test
    fun `fromBody rejects other error contracts`() {
        // Same Shape B key, different contract (e.g. rate limiting) —
        // must NOT be mistaken for the chat quota.
        assertNull(ChatLimitNotice.fromBody("""{"error": "Too many requests"}"""))
    }

    @Test
    fun `fromBody rejects garbage and blanks`() {
        assertNull(ChatLimitNotice.fromBody(null))
        assertNull(ChatLimitNotice.fromBody(""))
        assertNull(ChatLimitNotice.fromBody("   "))
        assertNull(ChatLimitNotice.fromBody("not json at all"))
        assertNull(ChatLimitNotice.fromBody("""{"detail": "purchase not active"}"""))
    }

    // ── fromStreamErrorMessage ───────────────────────────────────────

    @Test
    fun `fromStreamErrorMessage parses the HTTP 402 stream error shape`() {
        val notice = ChatLimitNotice.fromStreamErrorMessage("HTTP 402: $quotaBody")
        assertEquals(3, notice?.limit)
        assertEquals("free", notice?.plan)
    }

    @Test
    fun `fromStreamErrorMessage rejects other status codes`() {
        // A 422 body could contain arbitrary JSON — the status gate must
        // fire before any parsing.
        assertNull(ChatLimitNotice.fromStreamErrorMessage("HTTP 422: $quotaBody"))
        assertNull(ChatLimitNotice.fromStreamErrorMessage("HTTP 500: $quotaBody"))
        assertNull(ChatLimitNotice.fromStreamErrorMessage("Timeout"))
        assertNull(ChatLimitNotice.fromStreamErrorMessage("Network error"))
    }

    @Test
    fun `fromStreamErrorMessage rejects a 402 without a parseable body`() {
        assertNull(ChatLimitNotice.fromStreamErrorMessage("HTTP 402"))
        assertNull(ChatLimitNotice.fromStreamErrorMessage("HTTP 402: not json"))
        // A billing-endpoint 402 (Shape A detail body) must not trigger
        // the chat paywall path.
        assertNull(
            ChatLimitNotice.fromStreamErrorMessage(
                """HTTP 402: {"detail": "purchase not active (SUBSCRIPTION_STATE_EXPIRED)"}""",
            ),
        )
    }
}
