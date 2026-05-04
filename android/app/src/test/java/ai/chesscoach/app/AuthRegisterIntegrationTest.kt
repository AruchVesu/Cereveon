package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpAuthApiClient.register] against a real local HTTP server.
 *
 * Contract reference: POST /auth/register (llm/seca/auth/router.py).
 * Same response shape as POST /auth/login.
 *
 * Invariants pinned
 * -----------------
 *  1.  INT_REG_METHOD            request method is POST.
 *  2.  INT_REG_PATH              request path is /auth/register.
 *  3.  INT_REG_CONTENT_TYPE      Content-Type header is application/json.
 *  4.  INT_REG_EMAIL_IN_BODY     email field serialised in request body.
 *  5.  INT_REG_PASSWORD_IN_BODY  password field serialised in request body.
 *  6.  INT_REG_DEVICE_INFO       device_info field present in request body.
 *  7.  INT_REG_TOKEN_PARSED      access_token field deserialised on 200.
 *  8.  INT_REG_PLAYER_ID_PARSED  player_id field deserialised on 200.
 *  9.  INT_REG_HTTP_409          409 Conflict → ApiResult.HttpError(409).
 * 10.  INT_REG_HTTP_201          201 Created is also treated as success.
 * 11.  INT_REG_TIMEOUT           read timeout → ApiResult.Timeout.
 */
class AuthRegisterIntegrationTest {

    private lateinit var server: MockWebServer

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

    private fun client(readTimeoutMs: Int = 15_000) =
        HttpAuthApiClient(baseUrl = baseUrl(), readTimeoutMs = readTimeoutMs)

    companion object {
        private const val REGISTER_OK_BODY = """
{
  "access_token": "jwt-new-player-token",
  "player_id": "player-new-001",
  "token_type": "bearer"
}"""
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 1–3  HTTP method, path, Content-Type
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_REG_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        client().register("new@chess.com", "p@ss")
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `INT_REG_PATH - request path is slash auth slash register`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        client().register("new@chess.com", "p@ss")
        assertEquals("/auth/register", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `INT_REG_CONTENT_TYPE - Content-Type is application slash json`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        client().register("new@chess.com", "p@ss")
        val ct = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Content-Type") ?: ""
        assertTrue("Content-Type must contain application/json, was: $ct",
            "application/json" in ct)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4–6  Request body fields
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_REG_EMAIL_IN_BODY - email serialised in request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        client().register("alice@chess.com", "secret")
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("alice@chess.com", body.getString("email"))
    }

    @Test
    fun `INT_REG_PASSWORD_IN_BODY - password serialised in request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        client().register("alice@chess.com", "s3cr3t!")
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("s3cr3t!", body.getString("password"))
    }

    @Test
    fun `INT_REG_DEVICE_INFO - device_info field present in request JSON`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        client().register("alice@chess.com", "p@ss")
        val body = JSONObject(server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8())
        assertTrue("device_info field must be present", body.has("device_info"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 7–8  Response deserialisation
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_REG_TOKEN_PARSED - access_token deserialised on HTTP 200`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        val result = client().register("new@chess.com", "p@ss")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LoginResponse
        assertEquals("jwt-new-player-token", data.accessToken)
    }

    @Test
    fun `INT_REG_PLAYER_ID_PARSED - player_id deserialised on HTTP 200`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(REGISTER_OK_BODY))
        val result = client().register("new@chess.com", "p@ss")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LoginResponse
        assertEquals("player-new-001", data.playerId)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 9  Conflict
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_REG_HTTP_409 - duplicate email returns HttpError 409`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(409)
            .setBody("""{"detail":"Email already registered"}"""))
        val result = client().register("existing@chess.com", "p@ss")
        assertTrue("Expected HttpError, got: $result", result is ApiResult.HttpError)
        assertEquals(409, (result as ApiResult.HttpError).code)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 10  HTTP 201 also treated as success
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_REG_HTTP_201 - 201 Created is treated as success`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(201).setBody(REGISTER_OK_BODY))
        val result = client().register("new@chess.com", "p@ss")
        assertTrue("Expected Success on 201, got: $result", result is ApiResult.Success<*>)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 11  Timeout
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun `INT_REG_TIMEOUT - short read timeout returns ApiResult Timeout`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBodyDelay(500, TimeUnit.MILLISECONDS)
                .setBody(REGISTER_OK_BODY),
        )
        val result = client(readTimeoutMs = 1).register("new@chess.com", "p@ss")
        assertTrue("Expected Timeout, got: $result", result is ApiResult.Timeout)
    }
}
