package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for authentication data classes and JWT utility functions.
 *
 * Pure JVM — no Android framework dependencies.
 * JWT tokens are constructed from raw base64url payloads in each test.
 *
 * Invariants pinned
 * -----------------
 *  1.  LOGIN_REQUEST_FIELDS:         LoginRequest retains email, password, deviceInfo.
 *  2.  LOGIN_REQUEST_DEFAULT_DEVICE: LoginRequest.deviceInfo defaults to "".
 *  3.  LOGIN_REQUEST_EQUALITY:       Two identical LoginRequests are equal.
 *  4.  LOGIN_REQUEST_INEQUALITY:     LoginRequests differ when email differs.
 *  5.  LOGIN_REQUEST_COPY:           copy() produces independent instance.
 *  6.  LOGIN_RESPONSE_FIELDS:        LoginResponse retains all three fields.
 *  7.  LOGIN_RESPONSE_EQUALITY:      Two identical LoginResponses are equal.
 *  8.  LOGIN_RESPONSE_INEQUALITY:    LoginResponses differ when token differs.
 *  9.  LOGOUT_RESPONSE_STATUS:       LogoutResponse retains status field.
 * 10.  AUTH_STATE_AUTHENTICATED:     AuthState.Authenticated stores token and playerId.
 * 11.  AUTH_STATE_UNAUTHENTICATED:   AuthState.Unauthenticated is a singleton object.
 * 12.  AUTH_STATE_PATTERN:           when() matches both AuthState variants.
 * 13.  AUTH_STATE_NOT_EQUAL:         Authenticated and Unauthenticated are not equal.
 * 14.  PARSE_EXPIRY_FUTURE:          Future `exp` is parsed correctly.
 * 15.  PARSE_EXPIRY_PAST:            Past `exp` is parsed correctly.
 * 16.  PARSE_EXPIRY_MALFORMED:       Malformed token returns null.
 * 17.  PARSE_EXPIRY_WRONG_PARTS:     Token with != 3 parts returns null.
 * 18.  PARSE_EXPIRY_MISSING_FIELD:   Payload without `exp` returns null.
 * 19.  IS_EXPIRED_FUTURE:            Token with future exp → isJwtExpired = false.
 * 20.  IS_EXPIRED_PAST:              Token with past exp → isJwtExpired = true.
 * 21.  IS_EXPIRED_MALFORMED:         Malformed token → isJwtExpired = true (fail-closed).
 * 22.  PARSE_PLAYER_ID:              parseJwtPlayerId returns player_id claim.
 * 23.  PARSE_PLAYER_ID_MISSING:      Token without player_id returns null.
 * 24.  PARSE_PLAYER_ID_MALFORMED:    Malformed token returns null.
 * 25.  AUTHENTICATED_COPY:           AuthState.Authenticated copy semantics.
 */
class AuthApiModelsTest {

    // ------------------------------------------------------------------
    // Helpers — construct minimal JWTs from raw payloads
    // ------------------------------------------------------------------

    /**
     * Construct a three-part JWT with a base64url-encoded [payloadJson].
     * The header and signature parts are synthetic (not cryptographically valid)
     * because [parseJwtExpiry] and [parseJwtPlayerId] only inspect the payload.
     */
    private fun mockJwt(payloadJson: String): String {
        val encoder = java.util.Base64.getUrlEncoder().withoutPadding()
        val header = encoder.encodeToString("""{"alg":"HS256","typ":"JWT"}""".toByteArray())
        val payload = encoder.encodeToString(payloadJson.toByteArray(Charsets.UTF_8))
        return "$header.$payload.fakesignature"
    }

    private fun futureExp(): Long = System.currentTimeMillis() / 1000 + 3600  // +1 hour
    private fun pastExp(): Long = System.currentTimeMillis() / 1000 - 3600    // -1 hour

    // ------------------------------------------------------------------
    // 1–5  LoginRequest
    // ------------------------------------------------------------------

    @Test
    fun `LoginRequest retains email password and deviceInfo`() {
        val req = LoginRequest(email = "user@test.com", password = "secret", deviceInfo = "pixel7")
        assertEquals("user@test.com", req.email)
        assertEquals("secret", req.password)
        assertEquals("pixel7", req.deviceInfo)
    }

    @Test
    fun `LoginRequest deviceInfo defaults to empty string`() {
        val req = LoginRequest(email = "a@b.com", password = "p")
        assertEquals("", req.deviceInfo)
    }

    @Test
    fun `two identical LoginRequests are equal`() {
        val a = LoginRequest("a@b.com", "pass")
        val b = LoginRequest("a@b.com", "pass")
        assertEquals(a, b)
    }

    @Test
    fun `LoginRequests differ when email differs`() {
        val a = LoginRequest("x@test.com", "pass")
        val b = LoginRequest("y@test.com", "pass")
        assertNotEquals(a, b)
    }

    @Test
    fun `LoginRequest copy produces independent instance`() {
        val original = LoginRequest("a@b.com", "pass")
        val copy = original.copy(password = "new-pass")
        assertEquals("a@b.com", copy.email)
        assertEquals("new-pass", copy.password)
        assertEquals("pass", original.password) // original unchanged
    }

    // ------------------------------------------------------------------
    // 6–8  LoginResponse
    // ------------------------------------------------------------------

    @Test
    fun `LoginResponse retains all three fields`() {
        val resp = LoginResponse(accessToken = "tok.en.value", playerId = "pid-1", tokenType = "bearer")
        assertEquals("tok.en.value", resp.accessToken)
        assertEquals("pid-1", resp.playerId)
        assertEquals("bearer", resp.tokenType)
    }

    @Test
    fun `two identical LoginResponses are equal`() {
        val a = LoginResponse("t", "p", "bearer")
        val b = LoginResponse("t", "p", "bearer")
        assertEquals(a, b)
    }

    @Test
    fun `LoginResponses differ when accessToken differs`() {
        val a = LoginResponse("token-A", "pid", "bearer")
        val b = LoginResponse("token-B", "pid", "bearer")
        assertNotEquals(a, b)
    }

    // ------------------------------------------------------------------
    // 9  LogoutResponse
    // ------------------------------------------------------------------

    @Test
    fun `LogoutResponse retains status field`() {
        val resp = LogoutResponse(status = "logged_out")
        assertEquals("logged_out", resp.status)
    }

    // ------------------------------------------------------------------
    // 10–13  AuthState sealed class
    // ------------------------------------------------------------------

    @Test
    fun `AuthState Authenticated stores token and playerId`() {
        val state = AuthState.Authenticated(token = "jwt-token", playerId = "uuid-123")
        assertEquals("jwt-token", state.token)
        assertEquals("uuid-123", state.playerId)
    }

    @Test
    fun `AuthState Unauthenticated is a singleton object`() {
        val a: AuthState = AuthState.Unauthenticated
        val b: AuthState = AuthState.Unauthenticated
        assertSame(a, b)
    }

    @Test
    fun `when expression matches both AuthState variants`() {
        val states: List<AuthState> =
            listOf(
                AuthState.Authenticated("t", "p"),
                AuthState.Unauthenticated,
            )
        val kinds =
            states.map { s ->
                when (s) {
                    is AuthState.Authenticated -> "auth"
                    AuthState.Unauthenticated -> "unauth"
                }
            }
        assertEquals(listOf("auth", "unauth"), kinds)
    }

    @Test
    fun `Authenticated and Unauthenticated are never equal`() {
        assertNotEquals(AuthState.Authenticated("t", "p"), AuthState.Unauthenticated)
    }

    // ------------------------------------------------------------------
    // 14–18  parseJwtExpiry
    // ------------------------------------------------------------------

    @Test
    fun `parseJwtExpiry returns future exp for a valid future token`() {
        val exp = futureExp()
        val token = mockJwt("""{"player_id":"pid","exp":$exp}""")
        assertEquals(exp, parseJwtExpiry(token))
    }

    @Test
    fun `parseJwtExpiry returns past exp for an expired token`() {
        val exp = pastExp()
        val token = mockJwt("""{"player_id":"pid","exp":$exp}""")
        assertEquals(exp, parseJwtExpiry(token))
    }

    @Test
    fun `parseJwtExpiry returns null for a completely malformed token`() {
        assertNull(parseJwtExpiry("not.a.valid.jwt.at.all"))
    }

    @Test
    fun `parseJwtExpiry returns null for token with wrong number of parts`() {
        assertNull(parseJwtExpiry("only.two"))
        assertNull(parseJwtExpiry("one"))
    }

    @Test
    fun `parseJwtExpiry returns null when payload has no exp field`() {
        val token = mockJwt("""{"player_id":"pid","session_id":"sid"}""")
        assertNull(parseJwtExpiry(token))
    }

    // ------------------------------------------------------------------
    // 19–21  isJwtExpired
    // ------------------------------------------------------------------

    @Test
    fun `isJwtExpired returns false for a future token`() {
        val token = mockJwt("""{"exp":${futureExp()}}""")
        assertFalse("Non-expired token must return false", isJwtExpired(token))
    }

    @Test
    fun `isJwtExpired returns true for an expired token`() {
        val token = mockJwt("""{"exp":${pastExp()}}""")
        assertTrue("Expired token must return true", isJwtExpired(token))
    }

    @Test
    fun `isJwtExpired returns true for malformed token (fail-closed)`() {
        assertTrue("Malformed token must be treated as expired", isJwtExpired("bad.token"))
    }

    // ------------------------------------------------------------------
    // 22–24  parseJwtPlayerId
    // ------------------------------------------------------------------

    @Test
    fun `parseJwtPlayerId returns player_id from valid token`() {
        val token = mockJwt("""{"player_id":"uuid-abc-123","exp":${futureExp()}}""")
        assertEquals("uuid-abc-123", parseJwtPlayerId(token))
    }

    @Test
    fun `parseJwtPlayerId returns null when field is absent`() {
        val token = mockJwt("""{"exp":${futureExp()}}""")
        assertNull(parseJwtPlayerId(token))
    }

    @Test
    fun `parseJwtPlayerId returns null for malformed token`() {
        assertNull(parseJwtPlayerId("not-a-jwt"))
    }

    // ------------------------------------------------------------------
    // 25  AuthState.Authenticated copy
    // ------------------------------------------------------------------

    @Test
    fun `AuthState Authenticated copy produces independent instance`() {
        val original = AuthState.Authenticated(token = "old-token", playerId = "pid")
        val copy = original.copy(token = "new-token")
        assertEquals("new-token", copy.token)
        assertEquals("pid", copy.playerId)
        assertEquals("old-token", original.token) // original unchanged
    }
}
