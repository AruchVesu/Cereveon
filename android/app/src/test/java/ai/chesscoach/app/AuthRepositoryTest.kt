package ai.chesscoach.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Unit tests for [AuthRepository] using a pure in-memory [TokenStorage] fake.
 *
 * [EncryptedTokenStorage] requires a real Android Keystore and cannot run on
 * the JVM. The [InMemoryTokenStorage] test double exercises all [AuthRepository]
 * logic (save / load / clear / isLoggedIn / authState) without any Android dep.
 *
 * Invariants pinned
 * -----------------
 *  1.  INITIAL_NOT_LOGGED_IN:       New repository reports not logged in.
 *  2.  SAVE_THEN_GET:               saveToken then getToken returns the token.
 *  3.  SAVE_BLANK_THROWS:           saveToken with blank token throws IAE.
 *  4.  CLEAR_RETURNS_NULL:          clearToken makes getToken return null.
 *  5.  IS_LOGGED_IN_NO_TOKEN:       isLoggedIn is false when no token stored.
 *  6.  IS_LOGGED_IN_FUTURE:         isLoggedIn is true for non-expired token.
 *  7.  IS_LOGGED_IN_EXPIRED:        isLoggedIn is false for expired token.
 *  8.  IS_LOGGED_IN_AFTER_CLEAR:    isLoggedIn is false after clearToken.
 *  9.  AUTH_STATE_UNAUTHENTICATED:  authState() = Unauthenticated when no token.
 * 10.  AUTH_STATE_AUTHENTICATED:    authState() = Authenticated for valid token.
 * 11.  AUTH_STATE_PLAYER_ID:        authState() parses player_id from JWT.
 * 12.  AUTH_STATE_EXPIRED:          authState() = Unauthenticated for expired token.
 * 13.  OVERWRITE_TOKEN:             Second saveToken overwrites the first.
 * 14.  SAVE_WHITESPACE_THROWS:      saveToken with whitespace-only throws IAE.
 * 15.  IS_LOGGED_IN_MALFORMED:      isLoggedIn is false for unparseable token.
 * 16.  CLEAR_IDEMPOTENT:            clearToken twice does not throw.
 * 17.  GET_AFTER_CLEAR_IS_NULL:     getToken is null after clear.
 * 18.  AUTHENTICATED_HOLDS_TOKEN:   authState() Authenticated stores the raw token.
 * 19.  SAVE_UNICODE_TOKEN:          Token with unicode characters round-trips.
 * 20.  AUTH_STATE_AFTER_OVERWRITE:  authState reflects the most-recent token.
 * 21.  KEYSTORE_CORRUPT_GET_NULL:   getToken() returns null when storage.load() throws.
 * 22.  KEYSTORE_CORRUPT_AUTH_UNAUTH: authState() returns Unauthenticated when load throws.
 * 23.  KEYSTORE_CORRUPT_CLEARS:     storage.clear() is called when load() throws.
 */
class AuthRepositoryTest {

    // ------------------------------------------------------------------
    // Test doubles
    // ------------------------------------------------------------------

    /** Pure in-memory [TokenStorage] — no Android dependencies. */
    private class InMemoryTokenStorage : TokenStorage {
        private var stored: String? = null
        override fun save(token: String) { stored = token }
        override fun load(): String? = stored
        override fun clear() { stored = null }
    }

    /**
     * [TokenStorage] that throws on [load], simulating Android Keystore corruption.
     * Tracks whether [clear] was called so tests can assert the recovery path.
     */
    private class ThrowingTokenStorage : TokenStorage {
        var clearCalled = false
        override fun save(token: String) {}
        override fun load(): String? = throw RuntimeException("Keystore corrupted")
        override fun clear() { clearCalled = true }
    }

    // ------------------------------------------------------------------
    // JWT factory helpers
    // ------------------------------------------------------------------

    private fun mockJwt(payloadJson: String): String {
        val enc = java.util.Base64.getUrlEncoder().withoutPadding()
        val header = enc.encodeToString("""{"alg":"HS256","typ":"JWT"}""".toByteArray())
        val payload = enc.encodeToString(payloadJson.toByteArray(Charsets.UTF_8))
        return "$header.$payload.sig"
    }

    private fun futureToken(playerId: String = "player-1"): String =
        mockJwt("""{"player_id":"$playerId","exp":${System.currentTimeMillis() / 1000 + 3600}}""")

    private fun expiredToken(): String =
        mockJwt("""{"player_id":"player-x","exp":${System.currentTimeMillis() / 1000 - 3600}}""")

    // ------------------------------------------------------------------
    // Setup
    // ------------------------------------------------------------------

    private lateinit var storage: InMemoryTokenStorage
    private lateinit var repo: AuthRepository

    @Before
    fun setup() {
        storage = InMemoryTokenStorage()
        repo = AuthRepository(storage)
    }

    // ------------------------------------------------------------------
    // 1–4  Basic token lifecycle
    // ------------------------------------------------------------------

    @Test
    fun `new repository reports not logged in`() {
        assertFalse(repo.isLoggedIn())
    }

    @Test
    fun `saveToken then getToken returns the same token`() {
        val token = futureToken()
        repo.saveToken(token)
        assertEquals(token, repo.getToken())
    }

    @Test
    fun `saveToken with blank string throws IllegalArgumentException`() {
        try {
            repo.saveToken("   ")
            throw AssertionError("Expected IllegalArgumentException was not thrown")
        } catch (e: IllegalArgumentException) {
            // expected
        }
    }

    @Test
    fun `clearToken makes getToken return null`() {
        repo.saveToken(futureToken())
        repo.clearToken()
        assertNull(repo.getToken())
    }

    // ------------------------------------------------------------------
    // 5–8  isLoggedIn
    // ------------------------------------------------------------------

    @Test
    fun `isLoggedIn is false when no token stored`() {
        assertFalse(repo.isLoggedIn())
    }

    @Test
    fun `isLoggedIn is true for a non-expired token`() {
        repo.saveToken(futureToken())
        assertTrue(repo.isLoggedIn())
    }

    @Test
    fun `isLoggedIn is false for an expired token`() {
        repo.saveToken(expiredToken())
        assertFalse(repo.isLoggedIn())
    }

    @Test
    fun `isLoggedIn is false after clearToken`() {
        repo.saveToken(futureToken())
        repo.clearToken()
        assertFalse(repo.isLoggedIn())
    }

    // ------------------------------------------------------------------
    // 9–12  authState
    // ------------------------------------------------------------------

    @Test
    fun `authState returns Unauthenticated when no token stored`() {
        val state = repo.authState()
        assertTrue(
            "Expected Unauthenticated, got $state",
            state is AuthState.Unauthenticated,
        )
    }

    @Test
    fun `authState returns Authenticated for a valid token`() {
        repo.saveToken(futureToken("uid-99"))
        val state = repo.authState()
        assertTrue(
            "Expected Authenticated, got $state",
            state is AuthState.Authenticated,
        )
    }

    @Test
    fun `authState Authenticated contains the parsed player_id`() {
        repo.saveToken(futureToken("uid-42"))
        val state = repo.authState() as AuthState.Authenticated
        assertEquals("uid-42", state.playerId)
    }

    @Test
    fun `authState returns Unauthenticated for an expired token`() {
        repo.saveToken(expiredToken())
        val state = repo.authState()
        assertTrue(
            "Expected Unauthenticated for expired token, got $state",
            state is AuthState.Unauthenticated,
        )
    }

    // ------------------------------------------------------------------
    // 13–17  Edge cases
    // ------------------------------------------------------------------

    @Test
    fun `second saveToken overwrites the first`() {
        val first = futureToken("p1")
        val second = futureToken("p2")
        repo.saveToken(first)
        repo.saveToken(second)
        assertEquals(second, repo.getToken())
    }

    @Test
    fun `saveToken with whitespace-only throws IllegalArgumentException`() {
        try {
            repo.saveToken("\t  \n")
            throw AssertionError("Expected IllegalArgumentException was not thrown")
        } catch (e: IllegalArgumentException) {
            // expected
        }
    }

    @Test
    fun `isLoggedIn is false for a malformed token string`() {
        storage.save("this-is-not-a-jwt")
        assertFalse(repo.isLoggedIn())
    }

    @Test
    fun `clearToken called twice does not throw`() {
        repo.saveToken(futureToken())
        repo.clearToken()
        repo.clearToken() // must not throw
        assertNull(repo.getToken())
    }

    @Test
    fun `getToken is null after clear even when token was previously saved`() {
        repeat(3) { repo.saveToken(futureToken()) }
        repo.clearToken()
        assertNull(repo.getToken())
    }

    // ------------------------------------------------------------------
    // 18–20  Completeness
    // ------------------------------------------------------------------

    @Test
    fun `authState Authenticated holds the raw token string`() {
        val token = futureToken("uid-1")
        repo.saveToken(token)
        val state = repo.authState() as AuthState.Authenticated
        assertEquals(token, state.token)
    }

    @Test
    fun `saveToken round-trips token with unicode characters`() {
        val token = mockJwt(
            """{"player_id":"uid-\u00e9\u00e0","exp":${System.currentTimeMillis() / 1000 + 3600}}""",
        )
        repo.saveToken(token)
        assertNotNull(repo.getToken())
        // isLoggedIn requires parseJwtExpiry to succeed with the stored token
        assertTrue(repo.isLoggedIn())
    }

    @Test
    fun `authState reflects the most-recent token after overwrite`() {
        repo.saveToken(futureToken("old-player"))
        repo.saveToken(futureToken("new-player"))
        val state = repo.authState() as AuthState.Authenticated
        assertEquals("new-player", state.playerId)
    }

    // ------------------------------------------------------------------
    // 21–23  Keystore corruption recovery
    // ------------------------------------------------------------------

    @Test
    fun `getToken returns null when storage load throws`() {
        // KEYSTORE_CORRUPT_GET_NULL: EncryptedSharedPreferences can throw on
        // Keystore corruption; getToken() must swallow the exception and return null
        // so callers never receive an unhandled crash.
        val repo = AuthRepository(ThrowingTokenStorage())
        assertNull(repo.getToken())
    }

    @Test
    fun `authState returns Unauthenticated when storage load throws`() {
        // KEYSTORE_CORRUPT_AUTH_UNAUTH: authState() must return Unauthenticated
        // (not crash) when the backing store throws, so the app redirects to login.
        val repo = AuthRepository(ThrowingTokenStorage())
        val state = repo.authState()
        assertTrue(
            "Expected Unauthenticated when keystore corrupted, got $state",
            state is AuthState.Unauthenticated,
        )
    }

    @Test
    fun `storage clear is called when load throws`() {
        // KEYSTORE_CORRUPT_CLEARS: after a load failure the corrupted credentials
        // must be cleared so subsequent launches do not loop on the exception.
        val throwingStorage = ThrowingTokenStorage()
        val repo = AuthRepository(throwingStorage)
        repo.getToken()
        assertTrue(
            "storage.clear() must be called when load() throws to evict corrupted data",
            throwingStorage.clearCalled,
        )
    }
}
