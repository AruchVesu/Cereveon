package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Unit tests for [LichessConnectViewModel].
 *
 * Pinned invariants
 * -----------------
 * VM_REGEX_ACCEPTS_LICHESS_SHAPE  isValidUsername accepts 2-30 char `[A-Za-z0-9_-]`.
 * VM_REGEX_REJECTS_OFF_SHAPE      isValidUsername rejects spaces, `/`, non-ASCII, etc.
 *
 * VM_STATUS_NOT_LINKED            backend `{"linked": false}` → UiState.NotLinked.
 * VM_STATUS_LINKED                backend linked-true response → UiState.Linked with counts.
 *
 * VM_LINK_CLIENT_VALIDATION       Off-shape username never reaches the client + surfaces
 *                                 USERNAME_INVALID error.
 * VM_LINK_SUCCESS                 Successful link → UiState.Linked with calibration set.
 * VM_LINK_HTTP_404                404 → USERNAME_NOT_FOUND error; state reverts.
 * VM_LINK_HTTP_409                409 → ALREADY_LINKED_TO_OTHER_PLAYER error; state reverts.
 *
 * VM_IMPORT_REQUIRES_LINK         Calling importGames in NotLinked state surfaces NOT_LINKED
 *                                 without hitting the client.
 * VM_IMPORT_SUCCESS               Successful import merges counts into the Linked state
 *                                 and clears the calibration banner.
 *
 * VM_UNLINK_SUCCESS               Successful unlink → UiState.NotLinked.
 *
 * VM_TOKEN_MISSING                Missing token (logged-out player) → UNAUTHENTICATED
 *                                 without hitting the client.
 *
 * VM_LOADING_PRESERVES_PREVIOUS   Loading.previousState carries forward whatever was visible
 *                                 so the UI can render under the spinner.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LichessConnectViewModelTest {

    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = UnconfinedTestDispatcher(scheduler)

    @Before fun setUp() { Dispatchers.setMain(testDispatcher) }
    @After  fun tearDown() { Dispatchers.resetMain() }

    // ─────────────────────────────────────────────────────────────────────
    // Test doubles
    // ─────────────────────────────────────────────────────────────────────

    /** In-memory [AuthRepository] backed by a [FakeTokenStorage]. */
    private fun fakeAuthRepo(token: String? = "test-token"): AuthRepository {
        val storage = FakeTokenStorage()
        if (token != null) storage.save(token)
        return AuthRepository(storage)
    }

    private class FakeTokenStorage(private var token: String? = null) : TokenStorage {
        override fun save(token: String) { this.token = token }
        override fun load(): String? = token
        override fun clear() { token = null }
    }

    /**
     * Fake [LichessApiClient] that returns canned responses.  Each method
     * has a slot the test can override; defaults to HttpError(501) so an
     * unexpected call is loud.
     */
    private class FakeLichessClient : LichessApiClient {
        var statusResponse: ApiResult<LichessStatusResponse> = ApiResult.HttpError(501)
        var linkResponse: ApiResult<LichessLinkResponse> = ApiResult.HttpError(501)
        var importResponse: ApiResult<LichessImportResponse> = ApiResult.HttpError(501)
        var unlinkResponse: ApiResult<LichessUnlinkResponse> = ApiResult.HttpError(501)

        var linkCalls = 0
        var importCalls = 0
        var unlinkCalls = 0
        var statusCalls = 0
        var capturedLinkUsername: String? = null
        var capturedImportMaxGames: Int? = null

        override suspend fun status(token: String): ApiResult<LichessStatusResponse> {
            statusCalls += 1
            return statusResponse
        }

        override suspend fun link(username: String, token: String): ApiResult<LichessLinkResponse> {
            linkCalls += 1
            capturedLinkUsername = username
            return linkResponse
        }

        override suspend fun importGames(
            token: String,
            maxGames: Int,
            rated: Boolean,
        ): ApiResult<LichessImportResponse> {
            importCalls += 1
            capturedImportMaxGames = maxGames
            return importResponse
        }

        override suspend fun unlink(token: String): ApiResult<LichessUnlinkResponse> {
            unlinkCalls += 1
            return unlinkResponse
        }
    }

    private fun newViewModel(
        client: FakeLichessClient = FakeLichessClient(),
        authRepo: AuthRepository = fakeAuthRepo(),
    ) = LichessConnectViewModel(
        client = client,
        authRepository = authRepo,
        ioDispatcher = testDispatcher,
    )

    // ─────────────────────────────────────────────────────────────────────
    // VM_REGEX_*
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_REGEX_ACCEPTS_LICHESS_SHAPE - common shapes accepted`() {
        listOf("DrNykterstein", "alice", "user_42", "a-b", "Ab", "x".repeat(30)).forEach {
            assertTrue("expected '$it' to be valid", LichessConnectViewModel.isValidUsername(it))
        }
    }

    @Test
    fun `VM_REGEX_REJECTS_OFF_SHAPE - off-shape rejected`() {
        listOf(
            "",                 // empty
            "a",                // too short
            "x".repeat(31),     // too long
            "with spaces",
            "ümlaut",           // non-ASCII
            "rot/13",           // slash
            "drop;table",       // semicolon
            "evil?inject",      // question mark
            "alice/../admin",   // path traversal shape
        ).forEach {
            assertFalse("expected '$it' to be rejected", LichessConnectViewModel.isValidUsername(it))
        }
    }

    // ─────────────────────────────────────────────────────────────────────
    // VM_STATUS_*
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_STATUS_NOT_LINKED - linked-false maps to NotLinked state`() = runTest(testDispatcher) {
        val client = FakeLichessClient().apply {
            statusResponse = ApiResult.Success(LichessStatusResponse(linked = false))
        }
        val vm = newViewModel(client)
        val states = mutableListOf<LichessConnectViewModel.UiState>()
        vm.onStateChanged = { states += it }

        vm.refreshStatus()

        assertTrue(
            "expected NotLinked at end, got $states",
            states.last() is LichessConnectViewModel.UiState.NotLinked,
        )
        assertEquals(1, client.statusCalls)
    }

    @Test
    fun `VM_STATUS_LINKED - linked-true populates handle and counts`() = runTest(testDispatcher) {
        val client = FakeLichessClient().apply {
            statusResponse = ApiResult.Success(
                LichessStatusResponse(
                    linked = true,
                    platform = "lichess",
                    externalUsername = "thibault",
                    linkedAt = "2026-05-18T20:06:21",
                    lastImportedAt = "2026-05-13T08:28:57",
                    importedGameCount = 5,
                )
            )
        }
        val vm = newViewModel(client)

        vm.refreshStatus()

        val state = vm.state as LichessConnectViewModel.UiState.Linked
        assertEquals("thibault", state.username)
        assertEquals(5, state.importedGameCount)
        assertEquals("2026-05-13T08:28:57", state.lastImportedAt)
    }

    // ─────────────────────────────────────────────────────────────────────
    // VM_LINK_*
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_LINK_CLIENT_VALIDATION - off-shape username never reaches client`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient()
            val vm = newViewModel(client)
            var errorKind: LichessConnectViewModel.ErrorKind? = null
            vm.onError = { errorKind = it }

            vm.link("ümlaut")

            assertEquals(0, client.linkCalls)
            assertEquals(LichessConnectViewModel.ErrorKind.USERNAME_INVALID, errorKind)
        }

    @Test
    fun `VM_LINK_SUCCESS - successful link transitions to Linked with calibration`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                linkResponse = ApiResult.Success(
                    LichessLinkResponse(
                        platform = "lichess",
                        externalUsername = "thibault",
                        linkedAt = "2026-05-18T20:06:21",
                        calibration = LichessCalibrationResult(
                            applied = true,
                            perf = "rapid",
                            rating = 1907f,
                            confidence = 0.85f,
                            gamesBasis = 894,
                            provisional = false,
                        ),
                    )
                )
            }
            val vm = newViewModel(client)

            vm.link("thibault")

            val state = vm.state as LichessConnectViewModel.UiState.Linked
            assertEquals("thibault", state.username)
            assertEquals(1907f, state.calibration?.rating)
            assertEquals("rapid", state.calibration?.perf)
            assertEquals("thibault", client.capturedLinkUsername)
        }

    @Test
    fun `VM_LINK_HTTP_404 - user not found surfaces USERNAME_NOT_FOUND`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                linkResponse = ApiResult.HttpError(404)
            }
            val vm = newViewModel(client)
            var errorKind: LichessConnectViewModel.ErrorKind? = null
            vm.onError = { errorKind = it }

            vm.link("ghost")

            assertEquals(LichessConnectViewModel.ErrorKind.USERNAME_NOT_FOUND, errorKind)
            // State reverts to whatever was visible before — Initial in this test.
            assertTrue(vm.state is LichessConnectViewModel.UiState.Initial)
        }

    @Test
    fun `VM_LINK_HTTP_409 - cross-player conflict surfaces ALREADY_LINKED`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                linkResponse = ApiResult.HttpError(409)
            }
            val vm = newViewModel(client)
            var errorKind: LichessConnectViewModel.ErrorKind? = null
            vm.onError = { errorKind = it }

            vm.link("alice")

            assertEquals(
                LichessConnectViewModel.ErrorKind.ALREADY_LINKED_TO_OTHER_PLAYER,
                errorKind,
            )
        }

    // ─────────────────────────────────────────────────────────────────────
    // VM_IMPORT_*
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_IMPORT_REQUIRES_LINK - importGames in NotLinked surfaces NOT_LINKED`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                statusResponse = ApiResult.Success(LichessStatusResponse(linked = false))
            }
            val vm = newViewModel(client)
            vm.refreshStatus()

            var errorKind: LichessConnectViewModel.ErrorKind? = null
            vm.onError = { errorKind = it }

            vm.importGames()

            assertEquals(LichessConnectViewModel.ErrorKind.NOT_LINKED, errorKind)
            assertEquals(0, client.importCalls)
        }

    @Test
    fun `VM_IMPORT_SUCCESS - inserted count adds to existing imported count`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                statusResponse = ApiResult.Success(
                    LichessStatusResponse(
                        linked = true,
                        externalUsername = "thibault",
                        importedGameCount = 10,
                    )
                )
                importResponse = ApiResult.Success(
                    LichessImportResponse(
                        inserted = 5,
                        skippedDuplicate = 0,
                        skippedInvalid = 0,
                        lastImportedAt = "2026-05-13T08:28:57",
                    )
                )
            }
            val vm = newViewModel(client)
            vm.refreshStatus()
            vm.importGames(maxGames = 25)

            val state = vm.state as LichessConnectViewModel.UiState.Linked
            assertEquals(15, state.importedGameCount)  // 10 + 5
            assertEquals("2026-05-13T08:28:57", state.lastImportedAt)
            assertNull("calibration banner should be cleared after import", state.calibration)
            assertEquals(25, client.capturedImportMaxGames)
        }

    // ─────────────────────────────────────────────────────────────────────
    // VM_UNLINK_*
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_UNLINK_SUCCESS - successful unlink transitions to NotLinked`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                statusResponse = ApiResult.Success(
                    LichessStatusResponse(
                        linked = true,
                        externalUsername = "thibault",
                        importedGameCount = 5,
                    )
                )
                unlinkResponse = ApiResult.Success(LichessUnlinkResponse(unlinked = true))
            }
            val vm = newViewModel(client)
            vm.refreshStatus()
            vm.unlink()

            assertTrue(
                "expected NotLinked, got ${vm.state}",
                vm.state is LichessConnectViewModel.UiState.NotLinked,
            )
        }

    // ─────────────────────────────────────────────────────────────────────
    // VM_TOKEN_*
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_TOKEN_MISSING - missing token surfaces UNAUTHENTICATED without hitting client`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient()
            val vm = newViewModel(client = client, authRepo = fakeAuthRepo(token = null))
            var errorKind: LichessConnectViewModel.ErrorKind? = null
            vm.onError = { errorKind = it }

            vm.refreshStatus()

            assertEquals(LichessConnectViewModel.ErrorKind.UNAUTHENTICATED, errorKind)
            assertEquals(0, client.statusCalls)
        }

    // ─────────────────────────────────────────────────────────────────────
    // VM_LOADING_*
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_LOADING_PRESERVES_PREVIOUS - Loading carries the prior state forward`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                statusResponse = ApiResult.Success(LichessStatusResponse(linked = false))
            }
            val vm = newViewModel(client)
            val states = mutableListOf<LichessConnectViewModel.UiState>()
            vm.onStateChanged = { states += it }

            vm.refreshStatus()

            // Sequence is: Loading(prev=Initial) → NotLinked.  Loading
            // carries Initial forward so the UI doesn't blank the
            // previous state.
            val loading = states.first() as LichessConnectViewModel.UiState.Loading
            assertTrue(loading.previousState is LichessConnectViewModel.UiState.Initial)
            assertTrue(states.last() is LichessConnectViewModel.UiState.NotLinked)
        }
}
