package com.cereveon.myapp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Parser table for [GameLimitNotice] — the client-side reading of the
 * entitlements 402 on POST /game/start (API_CONTRACTS.md §11 "Errors",
 * the free-tier 1-game/day hard block).
 *
 * The parser MUST return null for everything that isn't this exact
 * contract, because MainActivity probes every /game/start HttpError
 * with it before deciding to show the paywall — a false positive would
 * hijack an unrelated failure into the paywall.
 */
class GameLimitNoticeTest {

    private val gameBody =
        """{"error": "game_daily_limit", "plan": "free", "limit": 1, "used": 1, """ +
            """"upgrade": {"product": "pro_monthly"}}"""

    @Test
    fun `parses the documented game-limit contract`() {
        val notice = GameLimitNotice.fromBody(gameBody)
        assertEquals("game_daily_limit", notice?.error)
        assertEquals("free", notice?.plan)
        assertEquals(1, notice?.limit)
        assertEquals(1, notice?.used)
    }

    @Test
    fun `parses reset_at when present`() {
        // Rolling-24h window: the server names the unlock instant (the played
        // game + 24h) so the lock screen can count down (API_CONTRACTS.md §11).
        val withReset =
            """{"error": "game_daily_limit", "plan": "free", "limit": 1, "used": 1, """ +
                """"reset_at": "2026-07-23T11:59:00", "upgrade": {"product": "pro_monthly"}}"""
        assertEquals("2026-07-23T11:59:00", GameLimitNotice.fromBody(withReset)?.resetAt)
    }

    @Test
    fun `leaves reset_at null when the server omits it`() {
        assertNull(GameLimitNotice.fromBody(gameBody)?.resetAt)
    }

    @Test
    fun `ignores unknown keys like upgrade`() {
        assertEquals("free", GameLimitNotice.fromBody(gameBody)?.plan)
    }

    @Test
    fun `rejects the chat-limit contract`() {
        // Same Shape B envelope, DIFFERENT gate — must not cross-fire, or
        // a chat 402 leaking to a game path (or vice-versa) would show the
        // wrong surface.
        val chatBody = """{"error": "chat_daily_limit", "plan": "free", "limit": 3, "used": 3}"""
        assertNull(GameLimitNotice.fromBody(chatBody))
    }

    @Test
    fun `rejects the billing 402 Shape A body`() {
        assertNull(
            GameLimitNotice.fromBody(
                """{"detail": "purchase not active (SUBSCRIPTION_STATE_EXPIRED)"}""",
            ),
        )
    }

    @Test
    fun `rejects garbage and blanks`() {
        assertNull(GameLimitNotice.fromBody(null))
        assertNull(GameLimitNotice.fromBody(""))
        assertNull(GameLimitNotice.fromBody("   "))
        assertNull(GameLimitNotice.fromBody("not json at all"))
        assertNull(GameLimitNotice.fromBody("""{"error": "Too many requests"}"""))
    }
}
