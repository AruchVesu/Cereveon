package com.cereveon.myapp

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpLichessApiClient] against a real local
 * HTTP server.
 *
 * Contract references: llm/seca/lichess/router.py + docs/API_CONTRACTS.md §§27–30.
 * Auth: Authorization: Bearer <token> required on every endpoint.
 *
 * Pinned invariants
 * -----------------
 * STATUS_METHOD          GET /lichess/status uses HTTP GET.
 * STATUS_PATH            request path is /lichess/status.
 * STATUS_BEARER          Authorization Bearer header is sent.
 * STATUS_NOT_LINKED      `{"linked": false}` deserialises to linked=false.
 * STATUS_LINKED          full linked response deserialises counts + username.
 * STATUS_DISCONNECTED    reconnect fields parse; absent fields default to
 *                        connected (older servers can't trigger the UI).
 * STATUS_HTTP_401        401 → ApiResult.HttpError(401).
 * STATUS_ROTATES         X-Auth-Token in response is forwarded to tokenSink.
 *
 * LINK_METHOD            POST /lichess/link uses HTTP POST.
 * LINK_BODY              request body carries the OAuth code + code_verifier
 *                        (ownership proof; no self-asserted username).
 * LINK_CONTENT_TYPE      Content-Type: application/json is sent.
 * LINK_CALIBRATION       calibration sub-object deserialises with all fields.
 * LINK_HTTP_401          401 → ApiResult.HttpError(401) (Lichess rejected grant).
 * LINK_HTTP_502          502 → ApiResult.HttpError(502) (Lichess upstream).
 *
 * IMPORT_METHOD          POST /lichess/import uses HTTP POST.
 * IMPORT_QUERY_DEFAULT   default max_games=50 + rated=true on the query string.
 * IMPORT_QUERY_CUSTOM    custom max_games / rated propagate to the query string.
 * IMPORT_COUNTS_PARSED   inserted/skipped_duplicate/skipped_invalid parse cleanly.
 *
 * STARTIMPORT_202_IS_SUCCESS  v2 202 Accepted parses as Success (regression:
 *                             default successCodes={200} mapped every
 *                             successful import to HttpError(202)).
 * STARTIMPORT_200_STILL_SUCCESS  200 stays a success alongside 202.
 * STARTIMPORT_HTTP_401        auth failure still surfaces as HttpError(401).
 * STARTIMPORT_202_ROTATES     X-Auth-Token on the 202 reaches the tokenSink.
 *
 * UNLINK_METHOD          DELETE /lichess/link uses HTTP DELETE.
 * UNLINK_TRUE            `{"unlinked": true}` round-trips.
 * UNLINK_FALSE           `{"unlinked": false}` round-trips (idempotent).
 */
class LichessApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        // Bind to 127.0.0.1 explicitly so the test never opens a port
        // on a public interface during CI runs (memory note:
        // feedback_android_host_jvm_tests).
        server = MockWebServer()
        server.start(InetAddress.getByName("127.0.0.1"), 0)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    private fun client(
        readTimeoutMs: Int = 15_000,
        tokenSink: ((String) -> Unit)? = null,
    ) = HttpLichessApiClient(
        baseUrl = baseUrl(),
        readTimeoutMs = readTimeoutMs,
        tokenSink = tokenSink,
    )

    // ===========================================================================
    // Status
    // ===========================================================================

    @Test
    fun `STATUS_METHOD - request uses HTTP GET`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"linked":false}"""))
        client().status("tok")
        assertEquals("GET", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `STATUS_PATH - request path is slash lichess slash status`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"linked":false}"""))
        client().status("tok")
        assertEquals("/lichess/status", server.takeRequest(10, TimeUnit.SECONDS)!!.path)
    }

    @Test
    fun `STATUS_BEARER - Authorization Bearer header is sent`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"linked":false}"""))
        client().status("my-token")
        val header = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Authorization")
        assertEquals("Bearer my-token", header)
    }

    @Test
    fun `STATUS_NOT_LINKED - linked false parses without remaining fields`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"linked":false}"""))
        val result = client().status("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LichessStatusResponse
        assertEquals(false, data.linked)
        assertNull(data.externalUsername)
        assertEquals(0, data.importedGameCount)
    }

    @Test
    fun `STATUS_LINKED - full payload parses cleanly`() = runBlocking {
        val body = """
            {
              "linked": true,
              "platform": "lichess",
              "external_username": "thibault",
              "linked_at": "2026-05-18T20:06:21.675290",
              "last_imported_at": "2026-05-13T08:28:57.755000",
              "imported_game_count": 5
            }
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(body))
        val result = client().status("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LichessStatusResponse
        assertEquals(true, data.linked)
        assertEquals("thibault", data.externalUsername)
        assertEquals(5, data.importedGameCount)
        assertEquals("2026-05-13T08:28:57.755000", data.lastImportedAt)
    }

    @Test
    fun `STATUS_DISCONNECTED - reconnect fields parse and default to connected`() = runBlocking {
        // Newer server: disconnected=true after an import 404'd on the
        // linked account (API_CONTRACTS §29 reconnect flow).
        val brokenBody = """
            {
              "linked": true,
              "platform": "lichess",
              "external_username": "thibault",
              "imported_game_count": 5,
              "disconnected": true,
              "disconnected_at": "2026-07-16T09:00:00.000000"
            }
        """.trimIndent()
        server.enqueue(MockResponse().setResponseCode(200).setBody(brokenBody))
        val broken = (client().status("tok") as ApiResult.Success<*>).data as LichessStatusResponse
        assertEquals(true, broken.disconnected)
        assertEquals("2026-07-16T09:00:00.000000", broken.disconnectedAt)

        // Older server (fields absent): must default to connected so the
        // reconnect UI can never fire on a pre-reconnect-flow backend.
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"linked": true, "external_username": "thibault", "imported_game_count": 5}""",
            ),
        )
        val legacy = (client().status("tok") as ApiResult.Success<*>).data as LichessStatusResponse
        assertEquals(false, legacy.disconnected)
        assertNull(legacy.disconnectedAt)
    }

    @Test
    fun `STATUS_HTTP_401 - unauthenticated maps to HttpError 401`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401))
        val result = client().status("bad")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `STATUS_ROTATES - X-Auth-Token in response is forwarded to tokenSink`() = runBlocking {
        var captured: String? = null
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .addHeader("X-Auth-Token", "fresh-jwt-12345")
                .setBody("""{"linked":false}""")
        )
        client(tokenSink = { captured = it }).status("old-token")
        assertEquals("fresh-jwt-12345", captured)
    }

    // ===========================================================================
    // Link
    // ===========================================================================

    @Test
    fun `LINK_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LINK_OK_BODY))
        client().link("auth-code-xyz", "verifier-123", "tok")
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `LINK_BODY - request body carries the OAuth code and verifier`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LINK_OK_BODY))
        client().link("auth-code-xyz", "verifier-123", "tok")
        val body = server.takeRequest(10, TimeUnit.SECONDS)!!.body.readUtf8()
        assertTrue(
            "expected body to carry the auth code, got: $body",
            body.contains("\"code\":\"auth-code-xyz\""),
        )
        assertTrue(
            "expected body to carry the code_verifier, got: $body",
            body.contains("\"code_verifier\":\"verifier-123\""),
        )
    }

    @Test
    fun `LINK_CONTENT_TYPE - request sends application slash json`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LINK_OK_BODY))
        client().link("auth-code-xyz", "verifier-123", "tok")
        val ct = server.takeRequest(10, TimeUnit.SECONDS)!!.getHeader("Content-Type")
        assertEquals("application/json", ct)
    }

    @Test
    fun `LINK_CALIBRATION - calibration sub-object deserialises`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(LINK_OK_BODY))
        val result = client().link("auth-code-xyz", "verifier-123", "tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LichessLinkResponse
        assertEquals("thibault", data.externalUsername)
        assertEquals(true, data.calibration.applied)
        assertEquals("rapid", data.calibration.perf)
        assertEquals(1907f, data.calibration.rating)
        assertEquals(0.85f, data.calibration.confidence)
        assertEquals(894, data.calibration.gamesBasis)
        assertEquals(false, data.calibration.provisional)
    }

    @Test
    fun `LINK_HTTP_401 - rejected OAuth grant surfaces as 401`() = runBlocking {
        // Lichess refused the authorization code (expired / replayed /
        // wrong verifier); the router maps LichessOAuthError → 401.
        server.enqueue(MockResponse().setResponseCode(401))
        val result = client().link("expired-code", "verifier-123", "tok")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `LINK_HTTP_502 - lichess upstream failure surfaces as 502`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(502))
        val result = client().link("auth-code-xyz", "verifier-123", "tok")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(502, (result as ApiResult.HttpError).code)
    }

    // ===========================================================================
    // Import
    // ===========================================================================

    @Test
    fun `IMPORT_METHOD - request uses HTTP POST`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(IMPORT_OK_BODY))
        client().importGames("tok")
        assertEquals("POST", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `IMPORT_QUERY_DEFAULT - default max_games and rated land on the query string`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(IMPORT_OK_BODY))
        client().importGames("tok")
        val path = server.takeRequest(10, TimeUnit.SECONDS)!!.path
        assertNotNull(path)
        assertTrue("path=$path", path!!.startsWith("/lichess/import"))
        assertTrue("path=$path", path.contains("max_games=50"))
        assertTrue("path=$path", path.contains("rated=true"))
    }

    @Test
    fun `IMPORT_QUERY_CUSTOM - custom max_games and rated false propagate`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(IMPORT_OK_BODY))
        client().importGames("tok", maxGames = 25, rated = false)
        val path = server.takeRequest(10, TimeUnit.SECONDS)!!.path!!
        assertTrue("path=$path", path.contains("max_games=25"))
        assertTrue("path=$path", path.contains("rated=false"))
    }

    @Test
    fun `IMPORT_COUNTS_PARSED - inserted and skipped counts deserialise`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(IMPORT_OK_BODY))
        val result = client().importGames("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LichessImportResponse
        assertEquals(5, data.inserted)
        assertEquals(0, data.skippedDuplicate)
        assertEquals(0, data.skippedInvalid)
        assertEquals("2026-05-13T08:28:57.755000", data.lastImportedAt)
    }

    // ===========================================================================
    // startImport (v2 — async job, HTTP 202)
    // ===========================================================================

    @Test
    fun `STARTIMPORT_202_IS_SUCCESS - the v2 Accepted response parses as Success`() = runBlocking {
        // REGRESSION PIN: the v2 endpoint answers 202 + LichessImportAccepted
        // (startImport's own KDoc says so), but BaseHttpClient's default
        // successCodes is {200} — so every successful import mapped to
        // HttpError(202) → the "unknown error" toast, no progress UI, and
        // the games appearing "mysteriously" later via the job-resume path.
        server.enqueue(MockResponse().setResponseCode(202).setBody(START_IMPORT_ACCEPTED_BODY))
        val result = client().startImport("tok")
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LichessImportAccepted
        assertEquals("job-123", data.jobId)
        assertEquals("queued", data.status)
        assertEquals(50, data.targetMaxGames)
    }

    @Test
    fun `STARTIMPORT_200_STILL_SUCCESS - a 200 body keeps parsing (idempotent-replay shape)`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(START_IMPORT_ACCEPTED_BODY))
        val result = client().startImport("tok")
        assertTrue("expected Success, got $result", result is ApiResult.Success<*>)
        assertEquals("job-123", ((result as ApiResult.Success<*>).data as LichessImportAccepted).jobId)
    }

    @Test
    fun `STARTIMPORT_HTTP_401 - auth failures still surface as HttpError`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"expired"}"""))
        val result = client().startImport("tok")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `STARTIMPORT_202_ROTATES - X-Auth-Token on the 202 reaches the tokenSink`() = runBlocking {
        // Rotation rides refreshOnSuccess(): now that 202 IS a success,
        // the rotated JWT must be forwarded exactly like on a 200.
        var rotated: String? = null
        server.enqueue(
            MockResponse()
                .setResponseCode(202)
                .setHeader("X-Auth-Token", "fresh-jwt")
                .setBody(START_IMPORT_ACCEPTED_BODY)
        )
        client(tokenSink = { rotated = it }).startImport("tok")
        assertEquals("fresh-jwt", rotated)
    }

    // ===========================================================================
    // Unlink
    // ===========================================================================

    @Test
    fun `UNLINK_METHOD - request uses HTTP DELETE`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"unlinked":true}"""))
        client().unlink("tok")
        assertEquals("DELETE", server.takeRequest(10, TimeUnit.SECONDS)!!.method)
    }

    @Test
    fun `UNLINK_TRUE - unlinked true parses`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"unlinked":true}"""))
        val result = client().unlink("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LichessUnlinkResponse
        assertEquals(true, data.unlinked)
    }

    @Test
    fun `UNLINK_FALSE - unlinked false parses (idempotent path)`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"unlinked":false}"""))
        val result = client().unlink("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as LichessUnlinkResponse
        assertEquals(false, data.unlinked)
    }

    companion object {
        private const val LINK_OK_BODY = """
            {
              "platform": "lichess",
              "external_username": "thibault",
              "linked_at": "2026-05-18T20:06:21.675290",
              "calibration": {
                "applied": true,
                "perf": "rapid",
                "rating": 1907.0,
                "confidence": 0.85,
                "games_basis": 894,
                "provisional": false
              }
            }
        """

        private const val IMPORT_OK_BODY = """
            {
              "inserted": 5,
              "skipped_duplicate": 0,
              "skipped_invalid": 0,
              "last_imported_at": "2026-05-13T08:28:57.755000"
            }
        """

        // The v2 202 payload — llm/seca/lichess/router.py startImport
        // (docs/API_CONTRACTS.md §29, async import job).
        private const val START_IMPORT_ACCEPTED_BODY = """
            {
              "job_id": "job-123",
              "status": "queued",
              "inserted": 0,
              "skipped_duplicate": 0,
              "skipped_invalid": 0,
              "target_max_games": 50,
              "created_at": "2026-07-14T20:00:00Z",
              "updated_at": "2026-07-14T20:00:00Z"
            }
        """
    }
}
