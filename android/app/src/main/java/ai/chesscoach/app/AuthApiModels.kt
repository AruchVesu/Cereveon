package ai.chesscoach.app

/**
 * Typed request/response models for the backend authentication endpoints.
 *
 * Pure Kotlin — no Android or org.json dependencies; fully JVM-testable.
 * JSON serialisation/deserialisation is handled in [HttpAuthApiClient].
 */

/**
 * Request body for POST /auth/login.
 *
 * Backend field mapping: email → email, password → password, deviceInfo → device_info.
 */
data class LoginRequest(
    val email: String,
    val password: String,
    /** Device fingerprint forwarded to the backend session record. */
    val deviceInfo: String = "",
)

/**
 * Typed response from POST /auth/login and POST /auth/register.
 *
 * Backend field names: access_token, player_id, token_type.
 */
data class LoginResponse(
    val accessToken: String,
    val playerId: String,
    val tokenType: String,
)

/** Response from POST /auth/logout. Backend returns {"status": "logged_out"}. */
data class LogoutResponse(val status: String)

/**
 * Response from GET /auth/me.
 *
 * Returns the authenticated player's current profile.  Used to sync the
 * rating display at cold-start without waiting for a /game/finish round.
 *
 * Backend field names: id, email, rating, confidence.
 */
data class MeResponse(
    val id: String,
    val email: String,
    val rating: Float,
    val confidence: Float,
    /**
     * Per-skill weakness scores from the SECA skill tracker.
     * Keys are skill names (e.g. "tactics", "endgame"); values are 0.0–1.0
     * where higher means more weakness in that area.
     * Empty when the player has no game history yet.
     */
    val skillVector: Map<String, Float> = emptyMap(),
)

/**
 * Current authentication state of the user in the application.
 *
 * Callers must handle both variants; use an exhaustive `when` expression.
 */
sealed class AuthState {
    /** User is logged in and holds a non-expired [token]. */
    data class Authenticated(val token: String, val playerId: String) : AuthState()

    /** User is not logged in, or the stored token has expired. */
    object Unauthenticated : AuthState()
}

// ---------------------------------------------------------------------------
// JWT utility functions — pure JVM; no Android dependencies
// ---------------------------------------------------------------------------

/**
 * Parse the `exp` (expiry) Unix timestamp from a JWT payload without full
 * signature validation. Returns null if the token is structurally malformed
 * or does not contain an `exp` field.
 *
 * Uses [java.util.Base64] (Java 8+), available in both JVM unit tests and
 * the Android runtime, so no additional dependencies are required.
 */
fun parseJwtExpiry(token: String): Long? {
    return try {
        val parts = token.split(".")
        if (parts.size != 3) return null
        // Base64url padding: must be a multiple of 4.
        val padded = parts[1].padEnd((parts[1].length + 3) / 4 * 4, '=')
        val payloadBytes = java.util.Base64.getUrlDecoder().decode(padded)
        val payload = String(payloadBytes, Charsets.UTF_8)
        Regex(""""exp"\s*:\s*(\d+)""").find(payload)?.groupValues?.get(1)?.toLongOrNull()
    } catch (_: Exception) {
        null
    }
}

/**
 * Parse the `player_id` claim from a JWT payload without full signature
 * validation. Returns null if the token is malformed or lacks the field.
 */
fun parseJwtPlayerId(token: String): String? {
    return try {
        val parts = token.split(".")
        if (parts.size != 3) return null
        val padded = parts[1].padEnd((parts[1].length + 3) / 4 * 4, '=')
        val payloadBytes = java.util.Base64.getUrlDecoder().decode(padded)
        val payload = String(payloadBytes, Charsets.UTF_8)
        Regex(""""player_id"\s*:\s*"([^"]+)"""").find(payload)?.groupValues?.get(1)
    } catch (_: Exception) {
        null
    }
}

/**
 * Returns true if the JWT [token] is expired (i.e., its `exp` claim is in
 * the past, using the current system clock). Returns true (fail-closed) for
 * any malformed or unsigned token — the server always performs authoritative
 * validation; this check is only a client-side optimisation to avoid sending
 * known-expired tokens.
 */
fun isJwtExpired(token: String): Boolean {
    val exp = parseJwtExpiry(token) ?: return true
    return System.currentTimeMillis() / 1000 >= exp
}
