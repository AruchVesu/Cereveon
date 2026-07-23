package com.cereveon.myapp

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Typed request/response models for the backend authentication endpoints.
 *
 * Sprint 4.3.C migrated these off hand-rolled ``org.json.JSONObject``
 * parsing onto kotlinx-serialization.  ``@SerialName`` annotations
 * preserve the snake_case wire format the FastAPI backend emits while
 * keeping the Kotlin properties camelCase.
 */

/**
 * Request body for POST /auth/login.
 */
@Serializable
data class LoginRequest(
    val email: String,
    val password: String,
    /** Device fingerprint forwarded to the backend session record. */
    @SerialName("device_info") val deviceInfo: String = "",
)

/**
 * Typed response from POST /auth/login and POST /auth/register.
 */
@Serializable
data class LoginResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("player_id") val playerId: String,
    @SerialName("token_type") val tokenType: String = "bearer",
)

/** Response from POST /auth/logout. Backend returns {"status": "logged_out"}. */
@Serializable
data class LogoutResponse(val status: String)

/**
 * Response from GET /auth/me.
 *
 * Returns the authenticated player's current profile.  Used to sync the
 * rating display at cold-start without waiting for a /game/finish round.
 */
@Serializable
data class MeResponse(
    val id: String = "",
    val email: String = "",
    val rating: Float = 0f,
    val confidence: Float = 0f,
    /**
     * Per-skill weakness scores from the SECA skill tracker.
     * Keys are skill names (e.g. "tactics", "endgame"); values are 0.0–1.0
     * where higher means more weakness in that area.
     * Empty when the player has no game history yet.
     */
    @SerialName("skill_vector") val skillVector: Map<String, Float> = emptyMap(),
    /**
     * Monotonic XP counter incremented when the player completes a training
     * exercise (seed = replay of an engine-flagged mistake; derivatives =
     * weekly micro-tasks of the same mistake pattern in new positions).
     *
     * Replaces the user-visible Elo rating on the Home screen — ``rating``
     * and ``confidence`` are still returned because they drive adaptive
     * opponent selection internally, but they are no longer displayed.
     */
    @SerialName("training_xp") val trainingXp: Int = 0,
)

/**
 * Request body for POST /auth/register — same shape as /auth/login.
 * (The previous hand-rolled client inlined the JSON body; this class
 * lets every endpoint go through ``ApiJson.encodeToString`` uniformly.)
 *
 * Default for [deviceInfo] is empty so that the explicit ``"android"``
 * the production client passes is *not* equal to the default — with
 * ``ApiJson.encodeDefaults = false`` a value equal to the declared
 * default would be stripped from the wire payload (regression caught
 * by ``INT_REG_DEVICE_INFO``).
 */
@Serializable
data class RegisterRequest(
    val email: String,
    val password: String,
    @SerialName("device_info") val deviceInfo: String = "",
)

/**
 * Request body for POST /auth/lichess — "Sign in with Lichess".
 *
 * [code] is the one-time OAuth authorization code from the Lichess
 * redirect; [codeVerifier] is the PKCE verifier that [LichessOAuth]
 * generated for this attempt.  The SERVER performs the code exchange
 * (see `docs/API_CONTRACTS.md` §16a) so no Lichess token ever reaches
 * the device.  The response is a [LoginResponse] superset — the extra
 * `created` / `lichess_username` fields are ignored by the shared
 * [ApiJson] config (`ignoreUnknownKeys`).
 */
@Serializable
data class LichessLoginRequest(
    val code: String,
    @SerialName("code_verifier") val codeVerifier: String,
    /** Device fingerprint forwarded to the backend session record. */
    @SerialName("device_info") val deviceInfo: String = "",
)

/**
 * Request body for POST /auth/change-password.  Both fields are
 * length-bounded server-side (1000 char max).
 */
@Serializable
data class ChangePasswordRequest(
    @SerialName("current_password") val currentPassword: String,
    @SerialName("new_password") val newPassword: String,
)

/**
 * Request body for PATCH /auth/me — partial profile update.  Either
 * or both fields may be absent.  Sending both null produces a 400 from
 * the backend.  ``encodeDefaults = false`` on the shared
 * [ApiJson] config ensures null fields are stripped from the wire
 * payload so the server-side ``rating: float | None = None`` /
 * ``confidence: float | None = None`` validators get exactly the
 * keys the client intended to update.
 */
@Serializable
data class UpdateMeRequest(
    val rating: Float? = null,
    val confidence: Float? = null,
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
