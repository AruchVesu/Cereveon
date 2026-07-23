package com.cereveon.myapp

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Integration tests for GET /auth/me/export — the client half of the
 * GDPR Art. 15/20 data export (docs/API_CONTRACTS.md §42) — using
 * [MockWebServer].
 *
 * The flow-level guarantees (fetch-then-pick, Success-only file write)
 * live in [DataExportFlows] view code that host-JVM tests can't
 * instantiate; they are pinned by SettingsDownloadDataSourcePinTest.
 * This file pins the wire contract of [AuthApiClient.exportData].
 *
 * Invariants pinned
 * -----------------
 *  1. EXPORT_GET_WITH_BEARER    GET /auth/me/export carries Authorization: Bearer <token>.
 *  2. EXPORT_PATH_CORRECT       Request path is /auth/me/export.
 *  3. EXPORT_METHOD_GET         Request method is GET.
 *  4. BODY_ROUND_TRIPS_RAW      200 → ApiResult.Success carrying the EXACT body
 *                               string — no client-side parsing (contract §42:
 *                               ``data`` is an open mapping; the client saves the
 *                               bytes the server produced).
 *  5. HTTP_401_SURFACES         401 → ApiResult.HttpError(401).
 *  6. HTTP_500_SURFACES         500 → ApiResult.HttpError(500).
 *  7. NETWORK_ERROR_SURFACES    Unreachable server → NetworkError/Timeout, never a throw.
 *  8. ROTATION_CONSUMED         An X-Auth-Token response header reaches the
 *                               tokenSink (normal authenticated-read rotation —
 *                               unlike deleteAccount, the session survives).
 */
class AuthExportDataIntegrationTest {

    private lateinit var server: MockWebServer

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    private val sampleDocument =
        """{"export_version":1,"player_id":"p1","data":{"players":[{"email":"a@b.c"}]}}"""

    private fun enqueueDocument(): MockResponse {
        val response = MockResponse().setResponseCode(200).setBody(sampleDocument)
        server.enqueue(response)
        return response
    }

    @Test
    fun `export request includes Authorization Bearer header`() = runBlocking {
        enqueueDocument()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        client.exportData("my-jwt-token")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer my-jwt-token", req.getHeader("Authorization"))
    }

    @Test
    fun `export request uses the correct path`() = runBlocking {
        enqueueDocument()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        client.exportData("tok")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("/auth/me/export", req.path)
    }

    @Test
    fun `export request method is GET`() = runBlocking {
        enqueueDocument()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        client.exportData("tok")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("GET", req.method)
    }

    @Test
    fun `HTTP 200 round-trips the raw document string`() = runBlocking {
        enqueueDocument()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        val result = client.exportData("tok")

        assertTrue("Expected Success, got $result", result is ApiResult.Success)
        assertEquals(
            "The client must save the EXACT bytes the server produced — " +
                "no parsing, no re-serialisation.",
            sampleDocument,
            (result as ApiResult.Success).data,
        )
    }

    @Test
    fun `HTTP 401 surfaces as HttpError 401`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Missing token"}"""))
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        val result = client.exportData("stale")

        assertTrue(result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `HTTP 500 surfaces as HttpError 500`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500))
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        val result = client.exportData("tok")

        assertTrue(result is ApiResult.HttpError)
        assertEquals(500, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `unreachable server surfaces as NetworkError not a throw`() = runBlocking {
        val client = HttpAuthApiClient(
            baseUrl = "http://127.0.0.1:1",
            connectTimeoutMs = 500,
            readTimeoutMs = 500,
        )

        val result = client.exportData("tok")

        assertTrue(
            "Expected NetworkError or Timeout, got $result",
            result is ApiResult.NetworkError || result is ApiResult.Timeout,
        )
    }

    @Test
    fun `X-Auth-Token rotation header reaches the tokenSink`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("X-Auth-Token", "rotated-jwt")
                .setBody(sampleDocument)
        )
        var sunk: String? = null
        val client = HttpAuthApiClient(
            baseUrl = baseUrl(),
            tokenSink = { newToken -> sunk = newToken },
        )

        val result = client.exportData("tok")

        assertTrue(result is ApiResult.Success)
        assertEquals(
            "exportData is a normal authenticated read — the rotation " +
                "header must be consumed (unlike deleteAccount, where the " +
                "session dies with the account).",
            "rotated-jwt",
            sunk,
        )
    }
}
