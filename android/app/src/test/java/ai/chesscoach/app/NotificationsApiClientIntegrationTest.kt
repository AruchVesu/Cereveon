package ai.chesscoach.app

import java.net.InetAddress
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Integration tests for [HttpNotificationsApiClient] against a real
 * local HTTP server.
 *
 * Contract references: llm/seca/notifications/router.py +
 * docs/API_CONTRACTS.md §40.  Auth: Authorization: Bearer <token>
 * required on every endpoint.
 *
 * Pinned invariants
 * -----------------
 * FEED_METHOD_PATH     GET /notifications with the Bearer header.
 * FEED_PARSES          full row (metadata, action, timestamps) + badge
 *                      count deserialise; unknown metadata keys are
 *                      tolerated (server-first evolution contract).
 * FEED_EMPTY           `{"notifications": [], "unread_count": 0}` parses.
 * FEED_HTTP_401        401 → ApiResult.HttpError(401).
 * FEED_ROTATES         X-Auth-Token in the response reaches tokenSink.
 *
 * READ_METHOD_PATH     POST /notifications/{id}/read.
 * READ_PARSES          `{"read": true, "unread_count": N}` round-trips.
 *
 * READALL_METHOD_PATH  POST /notifications/read-all.
 * READALL_PARSES       `{"marked": N, "unread_count": 0}` round-trips.
 *
 * DISMISS_METHOD_PATH  POST /notifications/{id}/dismiss.
 * DISMISS_PARSES       `{"dismissed": true, "unread_count": N}` round-trips.
 */
class NotificationsApiClientIntegrationTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        // 127.0.0.1 explicitly — never a public interface in CI
        // (memory note: feedback_android_host_jvm_tests).
        server = MockWebServer()
        server.start(InetAddress.getByName("127.0.0.1"), 0)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun baseUrl() = "http://127.0.0.1:${server.port}"

    private fun client(tokenSink: ((String) -> Unit)? = null) =
        HttpNotificationsApiClient(baseUrl = baseUrl(), tokenSink = tokenSink)

    private val fullFeedBody = """
        {
          "notifications": [
            {
              "id": "n-1",
              "type": "system_alert",
              "priority": "high",
              "title": "Lichess connection lost",
              "body": "We can't reach your Lichess account (alice) anymore.",
              "action": "lichess_reconnect",
              "action_label": "Reconnect",
              "metadata": {"lichess_username": "alice", "future_key": 1},
              "created_at": "2026-07-16T10:00:00.000000",
              "read_at": null
            },
            {
              "id": "n-2",
              "type": "game_analyzed",
              "priority": "medium",
              "title": "3 games reviewed",
              "body": "3 games from your Lichess history are ready to explore.",
              "action": "open_history",
              "action_label": "See games",
              "metadata": {"games_analyzed": 3},
              "created_at": "2026-07-15T09:00:00.000000",
              "read_at": "2026-07-15T10:00:00.000000"
            }
          ],
          "unread_count": 1
        }
    """.trimIndent()

    // ===========================================================================
    // Feed
    // ===========================================================================

    @Test
    fun `FEED_METHOD_PATH - GET slash notifications with Bearer header`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"notifications":[],"unread_count":0}"""),
        )
        client().feed("my-token")
        val request = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("GET", request.method)
        assertEquals("/notifications", request.path)
        assertEquals("Bearer my-token", request.getHeader("Authorization"))
    }

    @Test
    fun `FEED_PARSES - full rows and badge count deserialise`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody(fullFeedBody))
        val result = client().feed("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as NotificationsFeedResponse
        assertEquals(1, data.unreadCount)
        assertEquals(2, data.notifications.size)

        val alert = data.notifications[0]
        assertEquals("system_alert", alert.type)
        assertEquals("high", alert.priority)
        assertEquals(NotificationItem.ACTION_LICHESS_RECONNECT, alert.action)
        assertEquals("Reconnect", alert.actionLabel)
        assertEquals("alice", alert.metadata.lichessUsername)
        assertNull(alert.readAt)
        assertEquals(false, alert.isRead)

        val analyzed = data.notifications[1]
        assertEquals(NotificationItem.ACTION_OPEN_HISTORY, analyzed.action)
        assertEquals(3, analyzed.metadata.gamesAnalyzed)
        assertEquals(true, analyzed.isRead)
    }

    @Test
    fun `FEED_EMPTY - empty feed parses`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"notifications":[],"unread_count":0}"""),
        )
        val result = client().feed("tok")
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as NotificationsFeedResponse
        assertTrue(data.notifications.isEmpty())
        assertEquals(0, data.unreadCount)
    }

    @Test
    fun `FEED_HTTP_401 - auth failure surfaces as HttpError`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(401).setBody("""{"detail":"Missing token"}"""),
        )
        val result = client().feed("bad")
        assertTrue(result is ApiResult.HttpError)
        assertEquals(401, (result as ApiResult.HttpError).code)
    }

    @Test
    fun `FEED_ROTATES - X-Auth-Token reaches the tokenSink`() = runBlocking {
        var rotated: String? = null
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("X-Auth-Token", "fresh-jwt")
                .setBody("""{"notifications":[],"unread_count":0}"""),
        )
        client(tokenSink = { rotated = it }).feed("old-jwt")
        assertEquals("fresh-jwt", rotated)
    }

    // ===========================================================================
    // Mutations
    // ===========================================================================

    @Test
    fun `READ_METHOD_PATH and READ_PARSES`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"read":true,"unread_count":4}"""),
        )
        val result = client().markRead("n-1", "tok")
        val request = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", request.method)
        assertEquals("/notifications/n-1/read", request.path)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as NotificationReadResponse
        assertEquals(true, data.read)
        assertEquals(4, data.unreadCount)
    }

    @Test
    fun `READALL_METHOD_PATH and READALL_PARSES`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody("""{"marked":3,"unread_count":0}"""),
        )
        val result = client().markAllRead("tok")
        val request = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", request.method)
        assertEquals("/notifications/read-all", request.path)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as NotificationsReadAllResponse
        assertEquals(3, data.marked)
        assertEquals(0, data.unreadCount)
    }

    @Test
    fun `DISMISS_METHOD_PATH and DISMISS_PARSES`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setBody("""{"dismissed":true,"unread_count":2}"""),
        )
        val result = client().dismiss("n-9", "tok")
        val request = server.takeRequest(10, TimeUnit.SECONDS)!!
        assertEquals("POST", request.method)
        assertEquals("/notifications/n-9/dismiss", request.path)
        assertTrue(result is ApiResult.Success<*>)
        val data = (result as ApiResult.Success<*>).data as NotificationDismissResponse
        assertEquals(true, data.dismissed)
        assertEquals(2, data.unreadCount)
    }
}
