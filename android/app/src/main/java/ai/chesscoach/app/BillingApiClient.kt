package ai.chesscoach.app

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString

/**
 * Client for POST /billing/google/verify (docs/API_CONTRACTS.md §36).
 *
 * After the Play Billing flow completes, the purchase token is posted
 * here so the SERVER verifies it against the Google Play Developer API
 * and flips the authenticated player's plan.  The client never treats
 * a local purchase as entitlement — Pro state comes only from a
 * successful verify response (`plan == "pro"`), which is also why the
 * purchase is acknowledged only after this call succeeds (an
 * unacknowledged purchase is auto-refunded by Play, so a lost verify
 * can never silently eat the user's money).
 *
 * The route is `Depends(get_current_player)` on the server, so a valid
 * `Authorization: Bearer <jwt>` is required — same posture as
 * [HttpLiveMoveClient].
 */
interface BillingApiClient {

    /**
     * Verify [purchaseToken] for [productId] server-side.
     *
     * @return [ApiResult.Success] with the activated plan on HTTP 200;
     *         [ApiResult.HttpError] on non-200 (402 = Google says the
     *         token carries no entitlement, 502 = verification upstream
     *         unavailable — retry later, 503 = server has no Play
     *         credentials configured); [ApiResult.Timeout] /
     *         [ApiResult.NetworkError] for transport failures.
     */
    suspend fun verifyGooglePurchase(
        purchaseToken: String,
        productId: String,
    ): ApiResult<BillingVerifyResponse>
}

/**
 * Request body for POST /billing/google/verify.
 *
 * [purchaseToken]  From Play Billing's `Purchase.getPurchaseToken()`.
 * [productId]      The Play product that was bought ("pro_monthly" /
 *                  "pro_yearly" — see [PaywallActivity.PLAY_PRODUCT_IDS]).
 */
@Serializable
data class BillingVerifyRequest(
    @SerialName("purchase_token") val purchaseToken: String,
    @SerialName("product_id") val productId: String,
)

/**
 * Response from POST /billing/google/verify.
 *
 * [plan]       The plan now active for the player ("pro").  The client
 *              treats ONLY `plan == "pro"` as activation.
 * [productId]  Echo of the verified product.
 * [state]      Google's `subscriptionState` verbatim (diagnostic).
 */
@Serializable
data class BillingVerifyResponse(
    val plan: String = "",
    @SerialName("product_id") val productId: String = "",
    val state: String = "",
)

/**
 * Production implementation backed by [BaseHttpClient] /
 * [java.net.HttpURLConnection].  Each call opens its own connection;
 * the instance is thread-safe.
 *
 * @param baseUrl          Scheme + host + optional port, no trailing slash.
 * @param apiKey           Sent as the X-Api-Key request header.
 * @param tokenProvider    Supplier of the JWT Bearer token.  Required (no
 *                         default) — the route is a certain 401 without it,
 *                         same rationale as [HttpLiveMoveClient.tokenProvider].
 * @param tokenSink        Optional sink for the X-Auth-Token refresh header
 *                         so the verify call participates in JWT rotation
 *                         (docs/API_CONTRACTS.md §10).
 */
class HttpBillingApiClient(
    val baseUrl: String,
    val apiKey: String,
    val tokenProvider: () -> String?,
    val connectTimeoutMs: Int = BaseHttpClient.DEFAULT_CONNECT_TIMEOUT_MS,
    val readTimeoutMs: Int = BaseHttpClient.DEFAULT_READ_TIMEOUT_MS,
    val tokenSink: ((String) -> Unit)? = null,
) : BillingApiClient {

    companion object {
        private const val VERIFY_PATH = "/billing/google/verify"
    }

    private val http = BaseHttpClient(baseUrl, connectTimeoutMs, readTimeoutMs)

    override suspend fun verifyGooglePurchase(
        purchaseToken: String,
        productId: String,
    ): ApiResult<BillingVerifyResponse> = http.request(
        path = VERIFY_PATH,
        method = "POST",
        headers = buildMap {
            put("X-Api-Key", apiKey)
            tokenProvider.invoke()?.let { put("Authorization", "Bearer $it") }
        },
        body = ApiJson.encodeToString(
            BillingVerifyRequest(purchaseToken = purchaseToken, productId = productId)
        ),
        onResponse = { conn -> consumeRefreshedToken(conn, tokenSink) },
        parse = { body -> ApiJson.decodeFromString<BillingVerifyResponse>(body) },
    )
}
