package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Host-JVM tests for [LichessOAuth] — the PKCE material behind
 * "Sign in with Lichess".
 *
 * Invariants pinned
 * -----------------
 *  1.  OAUTH_S256_RFC_VECTOR       codeChallengeS256 reproduces the RFC 7636
 *                                  Appendix B verifier→challenge test vector.
 *  2.  OAUTH_VERIFIER_SHAPE        generated verifiers satisfy the RFC 7636
 *                                  §4.1 grammar (43–128 unreserved chars) —
 *                                  and the server's CODE_VERIFIER_RE mirror.
 *  3.  OAUTH_VERIFIER_UNIQUE       two verifiers never collide.
 *  4.  OAUTH_STATE_SHAPE           state values are url-safe and ≥ 43 chars.
 *  5.  OAUTH_URL_PARAMS            authorize URL carries exactly the PKCE
 *                                  parameter set (and no scope → identity-only).
 *  6.  OAUTH_URL_REDIRECT_ENCODED  redirect_uri is percent-encoded.
 *  7.  OAUTH_CONSTANTS_PINNED      client_id / redirect_uri match the server
 *                                  constants in llm/seca/lichess/client.py
 *                                  (docs/API_CONTRACTS.md §16a pins the pair).
 */
class LichessOAuthTest {

    /** Mirror of RFC 7636 §4.1 (and the server-side CODE_VERIFIER_RE). */
    private val verifierShape = Regex("^[A-Za-z0-9\\-._~]{43,128}$")

    // ─────────────────────────────────────────────────────────────────────────
    // 1  RFC 7636 Appendix B vector
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `OAUTH_S256_RFC_VECTOR - challenge matches the RFC 7636 appendix B vector`() {
        val verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        val expectedChallenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assertEquals(expectedChallenge, LichessOAuth.codeChallengeS256(verifier))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2–4  Generated material
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `OAUTH_VERIFIER_SHAPE - generated verifier satisfies RFC 7636 grammar`() {
        repeat(20) {
            val verifier = LichessOAuth.generateCodeVerifier()
            assertTrue(
                "verifier must match RFC 7636 shape, was: $verifier",
                verifierShape.matches(verifier),
            )
        }
    }

    @Test
    fun `OAUTH_VERIFIER_UNIQUE - two verifiers never collide`() {
        assertNotEquals(LichessOAuth.generateCodeVerifier(), LichessOAuth.generateCodeVerifier())
    }

    @Test
    fun `OAUTH_STATE_SHAPE - state is url-safe and long enough to resist guessing`() {
        val state = LichessOAuth.generateState()
        assertTrue("state too short: $state", state.length >= 43)
        assertTrue(
            "state must be url-safe base64, was: $state",
            Regex("^[A-Za-z0-9_-]+$").matches(state),
        )
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 5–6  Authorize URL
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `OAUTH_URL_PARAMS - authorize URL carries the full PKCE parameter set`() {
        val url = LichessOAuth.buildAuthorizeUrl(
            codeChallenge = "test-challenge",
            state = "test-state",
        )
        assertTrue(url.startsWith("https://lichess.org/oauth?"))
        assertTrue("response_type=code" in url)
        assertTrue("client_id=ai.chesscoach.app" in url)
        assertTrue("code_challenge_method=S256" in url)
        assertTrue("code_challenge=test-challenge" in url)
        assertTrue("state=test-state" in url)
        // Identity-only sign-in: no scopes are ever requested.
        assertFalse("scope must not be requested", "scope=" in url)
    }

    @Test
    fun `OAUTH_URL_REDIRECT_ENCODED - redirect_uri is percent-encoded`() {
        val url = LichessOAuth.buildAuthorizeUrl(codeChallenge = "c", state = "s")
        assertTrue(
            "redirect_uri must be percent-encoded, was: $url",
            "redirect_uri=ai.chesscoach.app%3A%2F%2Flichess-auth" in url,
        )
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 7  Cross-stack constant pins
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `OAUTH_CONSTANTS_PINNED - client_id and redirect_uri match the server pair`() {
        // Changing either side alone silently breaks the code exchange
        // (Lichess has no client registration to catch the drift) — update
        // llm/seca/lichess/client.py + docs/API_CONTRACTS.md §16a together.
        assertEquals("ai.chesscoach.app", LichessOAuth.CLIENT_ID)
        assertEquals("ai.chesscoach.app://lichess-auth", LichessOAuth.REDIRECT_URI)
        assertEquals(
            LichessOAuth.REDIRECT_URI,
            "${LichessOAuth.REDIRECT_SCHEME}://${LichessOAuth.REDIRECT_HOST}",
        )
    }
}
