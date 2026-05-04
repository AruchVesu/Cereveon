package ai.chesscoach.app

import java.net.HttpURLConnection

/**
 * Header the backend uses to hand back a freshly-minted JWT on every
 * successful authenticated response.  See [get_current_player] in
 * llm/seca/auth/router.py.
 *
 * The contract: an active client rotates the JWT it stores after
 * each authenticated call, so a stolen JWT is only valid until the
 * next legitimate call from the real owner happens — and the JWT
 * exp itself is bounded at 24 h regardless.
 */
internal const val X_AUTH_TOKEN_HEADER = "X-Auth-Token"

/**
 * Read [X_AUTH_TOKEN_HEADER] from a completed [HttpURLConnection]
 * response and pass any non-blank value to [sink].  No-op when:
 *   - [sink] is null (clients that don't participate in refresh)
 *   - the header is absent (unauthenticated routes / older server)
 *   - the value is blank (defensive)
 *
 * Header lookup is case-insensitive — Java's HttpURLConnection
 * normalises header names to whatever case the server returned.
 *
 * Designed to be called only after a successful response (HTTP 200);
 * server-side guards already prevent the header from being attached
 * to 4xx/5xx, so calling this on every code path would be safe but
 * a waste of one method invocation per failure.
 */
internal fun consumeRefreshedToken(conn: HttpURLConnection, sink: ((String) -> Unit)?) {
    if (sink == null) return
    val refreshed = conn.getHeaderField(X_AUTH_TOKEN_HEADER) ?: return
    if (refreshed.isBlank()) return
    sink(refreshed)
}
