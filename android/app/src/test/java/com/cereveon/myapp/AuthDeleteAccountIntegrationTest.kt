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
 * Integration tests for DELETE /auth/me — account erasure (GDPR Art. 17,
 * docs/API_CONTRACTS.md §41) — using [MockWebServer].
 *
 * The flow-level guarantees (confirmation gating, clear-local-state only
 * on success, 401 → login without claiming deletion) live in
 * [AccountFlows] view code that host-JVM tests can't instantiate; they
 * are pinned by SettingsDeleteAccountSourcePinTest.  This file pins the
 * wire contract of [AuthApiClient.deleteAccount] itself.
 *
 * Invariants pinned
 * -----------------
 *  1. DELETE_CALLED_WITH_BEARER  DELETE /auth/me carries Authorization: Bearer <token>.
 *  2. DELETE_PATH_CORRECT        Request path is /auth/me.
 *  3. DELETE_METHOD_DELETE       Request method is DELETE (real verb, no override header).
 *  4. NO_BODY_SENT               Request body is empty (contract §41: "Request body: Empty").
 *  5. SUCCESS_ON_200             200 {"status":"deleted"} → ApiResult.Success(Unit).
 *  6. HTTP_401_SURFACES          401 → ApiResult.HttpError(401) — session already dead;
 *                                the caller must NOT claim deletion happened.
 *  7. HTTP_500_SURFACES          500 → ApiResult.HttpError(500) — account still exists.
 *  8. NETWORK_ERROR_SURFACES     Unreachable server → ApiResult.NetworkError, never a throw.
 */
class AuthDeleteAccountIntegrationTest {

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

    private fun enqueueDeleted() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"deleted"}"""))
    }

    @Test
    fun `delete request includes Authorization Bearer header`() = runBlocking {
        enqueueDeleted()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        client.deleteAccount("my-jwt-token")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("Bearer my-jwt-token", req.getHeader("Authorization"))
    }

    @Test
    fun `delete request uses the correct path`() = runBlocking {
        enqueueDeleted()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        client.deleteAccount("tok")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("/auth/me", req.path)
    }

    @Test
    fun `delete request method is DELETE`() = runBlocking {
        enqueueDeleted()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        client.deleteAccount("tok")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("DELETE", req.method)
    }

    @Test
    fun `delete request sends no body`() = runBlocking {
        enqueueDeleted()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        client.deleteAccount("tok")

        val req = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals(0L, req.bodySize)
    }

    @Test
    fun `HTTP 200 with deleted status maps to Success`() = runBlocking {
        enqueueDeleted()
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        val result = client.deleteAccount("tok")

        assertTrue(
            "Expected ApiResult.Success on 200, got $result",
            result is ApiResult.Success,
        )
    }

    @Test
    fun `HTTP 401 surfaces as HttpError 401`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Session invalid"}"""))
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        val result = client.deleteAccount("stale-token")

        assertTrue("Expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `HTTP 500 surfaces as HttpError 500`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500))
        val client = HttpAuthApiClient(baseUrl = baseUrl())

        val result = client.deleteAccount("tok")

        assertTrue("Expected HttpError, got $result", result is ApiResult.HttpError)
        assertEquals(500, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `unreachable server surfaces as NetworkError not a throw`() = runBlocking {
        val client = HttpAuthApiClient(
            baseUrl = "http://127.0.0.1:1",
            connectTimeoutMs = 500,
            readTimeoutMs = 500,
        )

        val result = client.deleteAccount("tok")

        assertTrue(
            "Expected NetworkError or Timeout on unreachable server, got $result",
            result is ApiResult.NetworkError || result is ApiResult.Timeout,
        )
    }
}
