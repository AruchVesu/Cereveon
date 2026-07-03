package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpBillingApiClient] against MockWebServer.
 *
 * Contract reference: POST /billing/google/verify
 * (docs/API_CONTRACTS.md §36).  Auth: X-Api-Key + JWT Bearer (route is
 * `Depends(get_current_player)` server-side).
 *
 * Invariants pinned
 * -----------------
 *  1. INT_BILL_METHOD        request method is POST.
 *  2. INT_BILL_PATH          request path is /billing/google/verify.
 *  3. INT_BILL_CONTENT_TYPE  Content-Type is application/json.
 *  4. INT_BILL_API_KEY_SENT  X-Api-Key header present.
 *  5. INT_BILL_BEARER_SENT   Authorization: Bearer <jwt> present when the
 *                            tokenProvider returns a token.
 *  6. INT_BILL_BEARER_ABSENT Authorization absent when tokenProvider
 *                            returns null.
 *  7. INT_BILL_WIRE_SHAPE    body is EXACTLY {"purchase_token", "product_id"}
 *                            — snake_case, no extra keys (the server's
 *                            Pydantic model is the other side of this pin).
 *  8. INT_BILL_200_PARSED    200 body → ApiResult.Success with plan /
 *                            product_id / state mapped to camelCase fields.
 *  9. INT_BILL_402_HTTP_ERR  402 (not entitled) → ApiResult.HttpError(402).
 * 10. INT_BILL_503_HTTP_ERR  503 (unconfigured server) → HttpError(503).
 * 11. INT_BILL_TOKEN_SINK    X-Auth-Token response header lands in tokenSink
 *                            (JWT rotation participation, §10).
 */
class BillingApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    private val apiKey = "test-api-key-billing"

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start(InetAddress.getByName("127.0.0.1"), 0)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    private fun client(
        token: String? = "jwt-test-token",
        tokenSink: ((String) -> Unit)? = null,
    ) = HttpBillingApiClient(
        baseUrl = baseUrl(),
        apiKey = apiKey,
        tokenProvider = { token },
        tokenSink = tokenSink,
    )

    companion object {
        private const val VERIFY_OK_BODY = """
{
  "plan": "pro",
  "product_id": "pro_monthly",
  "state": "SUBSCRIPTION_STATE_ACTIVE"
}"""
    }

    @Test
    fun `request wire shape is exactly the documented contract`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(VERIFY_OK_BODY))

        client().verifyGooglePurchase("tok-abc-123", "pro_monthly")

        val recorded = server.takeRequest(10, TimeUnit.SECONDS)
        assertNotNull(recorded)
        recorded!!
        // 1 + 2 + 3 + 4 + 5
        assertEquals("POST", recorded.method)
        assertEquals("/billing/google/verify", recorded.path)
        assertTrue(
            "Content-Type must be application/json",
            recorded.getHeader("Content-Type")!!.startsWith("application/json"),
        )
        assertEquals(apiKey, recorded.getHeader("X-Api-Key"))
        assertEquals("Bearer jwt-test-token", recorded.getHeader("Authorization"))

        // 7 — exact snake_case shape, no extra keys.
        val body = JSONObject(recorded.body.readUtf8())
        assertEquals("tok-abc-123", body.getString("purchase_token"))
        assertEquals("pro_monthly", body.getString("product_id"))
        assertEquals(
            "verify body must carry exactly purchase_token + product_id",
            2, body.length(),
        )
    }

    @Test
    fun `bearer header is absent when token provider returns null`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(VERIFY_OK_BODY))

        client(token = null).verifyGooglePurchase("tok", "pro_monthly")

        val recorded = server.takeRequest(10, TimeUnit.SECONDS)
        assertNotNull(recorded)
        assertNull(
            "no Authorization header may be fabricated for a logged-out caller",
            recorded!!.getHeader("Authorization"),
        )
    }

    @Test
    fun `http 200 parses into Success with camelCase mapping`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(VERIFY_OK_BODY))

        val result = client().verifyGooglePurchase("tok", "pro_monthly")

        assertTrue("expected Success, got $result", result is ApiResult.Success)
        val data = (result as ApiResult.Success).data
        assertEquals("pro", data.plan)
        assertEquals("pro_monthly", data.productId)
        assertEquals("SUBSCRIPTION_STATE_ACTIVE", data.state)
    }

    @Test
    fun `http 402 not entitled maps to HttpError 402`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(402)
                .setBody("""{"detail": "purchase not active (SUBSCRIPTION_STATE_EXPIRED)"}"""),
        )

        val result = client().verifyGooglePurchase("tok", "pro_monthly")

        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(402, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `http 503 unconfigured maps to HttpError 503`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(503)
                .setBody("""{"detail": "purchase verification not configured"}"""),
        )

        val result = client().verifyGooglePurchase("tok", "pro_monthly")

        assertTrue("expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(503, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `x-auth-token response header reaches the token sink`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody(VERIFY_OK_BODY)
                .setHeader("X-Auth-Token", "rotated-jwt-42"),
        )
        var sunk: String? = null

        client(tokenSink = { sunk = it }).verifyGooglePurchase("tok", "pro_monthly")

        assertEquals("rotated-jwt-42", sunk)
    }
}
