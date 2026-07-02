package ai.chesscoach.app

import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * Pure-JVM helper for the Lichess OAuth 2.0 authorization-code + PKCE
 * flow (RFC 7636) behind the "Sign in with Lichess" button on
 * [LoginActivity].
 *
 * Division of labour (mirrors `docs/API_CONTRACTS.md` §16a):
 *
 *  - THIS side generates the `code_verifier`, derives the S256
 *    `code_challenge`, and opens the system browser at
 *    [AUTHORIZE_ENDPOINT] with the pinned [CLIENT_ID] / [REDIRECT_URI].
 *  - Lichess redirects to `ai.chesscoach.app://lichess-auth?code&state`,
 *    which [LichessAuthRedirectActivity] forwards to [LoginActivity].
 *  - The BACKEND performs the code exchange (`POST /auth/lichess` with
 *    `code` + `code_verifier`) so Lichess access tokens never live on
 *    the device.
 *
 * [CLIENT_ID] and [REDIRECT_URI] must byte-match the server constants in
 * `llm/seca/lichess/client.py` — Lichess accepts unregistered public
 * clients, so no upstream registration exists to catch a drift; the
 * exchange just fails with `invalid_grant`.
 *
 * No `scope` parameter is sent: the sign-in needs public identity only.
 *
 * Deliberately dependency-free (no AppAuth): `java.util.Base64` (minSdk
 * 26) + `java.security` keep every function host-JVM testable — see
 * `LichessOAuthTest` for the RFC 7636 Appendix B vector pin.
 */
object LichessOAuth {

    const val AUTHORIZE_ENDPOINT = "https://lichess.org/oauth"
    const val CLIENT_ID = "ai.chesscoach.app"
    const val REDIRECT_URI = "ai.chesscoach.app://lichess-auth"

    /** Scheme + host of [REDIRECT_URI], split for intent-data matching. */
    const val REDIRECT_SCHEME = "ai.chesscoach.app"
    const val REDIRECT_HOST = "lichess-auth"

    // 64 random bytes → 86 base64url chars, comfortably inside the RFC
    // 7636 §4.1 verifier bounds (43–128) with ~512 bits of entropy.
    private const val VERIFIER_BYTES = 64

    // The `state` value only guards the redirect against CSRF/mix-up;
    // 32 bytes (43 chars) is ample.
    private const val STATE_BYTES = 32

    /** Fresh RFC 7636 code verifier (base64url, unpadded, 86 chars). */
    fun generateCodeVerifier(random: SecureRandom = SecureRandom()): String =
        randomUrlSafe(VERIFIER_BYTES, random)

    /** Fresh opaque `state` value for the authorization request. */
    fun generateState(random: SecureRandom = SecureRandom()): String =
        randomUrlSafe(STATE_BYTES, random)

    /**
     * S256 code challenge: `BASE64URL-ENCODE(SHA256(ASCII(verifier)))`
     * per RFC 7636 §4.2 — no padding.
     */
    fun codeChallengeS256(codeVerifier: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(codeVerifier.toByteArray(Charsets.US_ASCII))
        return Base64.getUrlEncoder().withoutPadding().encodeToString(digest)
    }

    /**
     * Full authorization URL for the system browser.
     *
     * Built with plain string concatenation + [URLEncoder] (not
     * `android.net.Uri`) so the function stays host-JVM testable.  Every
     * value is URL-encoded defensively even though the generated ones
     * are already URL-safe base64.
     */
    fun buildAuthorizeUrl(codeChallenge: String, state: String): String =
        AUTHORIZE_ENDPOINT +
            "?response_type=code" +
            "&client_id=${encode(CLIENT_ID)}" +
            "&redirect_uri=${encode(REDIRECT_URI)}" +
            "&code_challenge_method=S256" +
            "&code_challenge=${encode(codeChallenge)}" +
            "&state=${encode(state)}"

    private fun randomUrlSafe(byteCount: Int, random: SecureRandom): String {
        val bytes = ByteArray(byteCount)
        random.nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8")
}
