package ai.chesscoach.app

/**
 * Manages the JWT lifecycle for the logged-in user.
 *
 * Wraps [TokenStorage] to provide semantic operations (save, retrieve, check
 * expiry, clear) without exposing raw storage details to callers.
 *
 * [isJwtExpired] is used for client-side optimisation only — the server
 * always performs authoritative JWT validation on every request.
 *
 * Thread safety: individual [TokenStorage] calls are atomic; composite
 * operations such as [isLoggedIn] are NOT atomic. Access only from the
 * main thread (or synchronise externally) if you share an instance.
 *
 * @param storage Backing store — use [EncryptedTokenStorage] in production,
 *                or an in-memory fake in JVM unit tests.
 */
class AuthRepository(private val storage: TokenStorage) {

    /**
     * Persist [token] to durable storage.
     * Call this immediately after a successful login or register response.
     *
     * @throws IllegalArgumentException if [token] is blank.
     */
    fun saveToken(token: String) {
        require(token.isNotBlank()) { "token must not be blank" }
        storage.save(token)
    }

    /**
     * Return the stored token, or null if the user has never logged in,
     * logged out, or the token was cleared for any other reason.
     *
     * Returns null (and clears storage) if the underlying [TokenStorage]
     * throws — e.g. on Android Keystore corruption. The caller receives null
     * exactly as if the user had never logged in, which causes [authState] to
     * return [AuthState.Unauthenticated] and redirect the user to login.
     *
     * The returned token may be expired — use [isLoggedIn] to combine
     * the presence check with an expiry check.
     */
    fun getToken(): String? = try {
        storage.load()
    } catch (e: Exception) {
        runCatching { storage.clear() }
        null
    }

    /**
     * Return the current [AuthState]:
     *  - [AuthState.Authenticated] when a non-expired token is stored.
     *  - [AuthState.Unauthenticated] otherwise.
     *
     * The `player_id` in [AuthState.Authenticated] is parsed from the JWT
     * payload without signature validation — treat it as informational only.
     */
    fun authState(): AuthState {
        val token = getToken() ?: return AuthState.Unauthenticated
        if (isJwtExpired(token)) return AuthState.Unauthenticated
        val playerId = parseJwtPlayerId(token) ?: ""
        return AuthState.Authenticated(token = token, playerId = playerId)
    }

    /**
     * Return true if a non-expired token is present in storage.
     *
     * This is a convenience wrapper over [authState]; prefer [authState]
     * when you also need the token or player ID.
     */
    fun isLoggedIn(): Boolean = authState() is AuthState.Authenticated

    /**
     * Remove the stored token, logging the user out on the client side.
     *
     * The caller is responsible for also calling the /auth/logout backend
     * endpoint to invalidate the server-side session.
     */
    fun clearToken() {
        storage.clear()
    }
}
