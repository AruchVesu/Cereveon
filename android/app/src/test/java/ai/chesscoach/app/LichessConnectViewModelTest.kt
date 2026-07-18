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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Unit tests for [LichessConnectViewModel].
 *
 * Pinned invariants
 * -----------------
 * VM_STATUS_NOT_LINKED            backend `{"linked": false}` → UiState.NotLinked.
 * VM_STATUS_LINKED                backend linked-true response → UiState.Linked with counts.
 *
 * (Linking is no longer a ViewModel operation: it runs as an OAuth
 * browser round-trip in LichessLinkFlow → LichessLinkRedirectActivity so
 * ownership is proven server-side.  The former VM_REGEX_* / VM_LINK_*
 * pins retired with the self-asserted-username path; the redirect
 * activity's CSRF-state + code-exchange contract is covered separately.)
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
     *
     * Carries v1 slots (importResponse) AND v2 slots (startImportResponse
     * + jobStatusResponse) so legacy tests can still exercise the
     * deprecated path if needed.  Production code paths exercise v2.
     */
    private class FakeLichessClient : LichessApiClient {
        var statusResponse: ApiResult<LichessStatusResponse> = ApiResult.HttpError(501)
        var importResponse: ApiResult<LichessImportResponse> = ApiResult.HttpError(501)
        var startImportResponse: ApiResult<LichessImportAccepted> = ApiResult.HttpError(501)
        var jobStatusResponse: ApiResult<LichessImportJobStatus> = ApiResult.HttpError(501)
        var unlinkResponse: ApiResult<LichessUnlinkResponse> = ApiResult.HttpError(501)

        var importCalls = 0
        var startImportCalls = 0
        var getImportJobCalls = 0
        var unlinkCalls = 0
        var statusCalls = 0
        var capturedImportMaxGames: Int? = null
        var capturedStartImportMaxGames: Int? = null
        var capturedGetImportJobId: String? = null

        override suspend fun status(token: String): ApiResult<LichessStatusResponse> {
            statusCalls += 1
            return statusResponse
        }

        // No link() override: the ViewModel no longer performs linking
        // (it's an OAuth browser round-trip now).  The interface default
        // (HttpError(501)) stands so an accidental call would be loud.

        @Suppress("OVERRIDE_DEPRECATION", "DEPRECATION")
        override suspend fun importGames(
            token: String,
            maxGames: Int,
            rated: Boolean,
        ): ApiResult<LichessImportResponse> {
            importCalls += 1
            capturedImportMaxGames = maxGames
            return importResponse
        }

        override suspend fun startImport(
            token: String,
            maxGames: Int,
            rated: Boolean,
        ): ApiResult<LichessImportAccepted> {
            startImportCalls += 1
            capturedStartImportMaxGames = maxGames
            return startImportResponse
        }

        override suspend fun getImportJob(
            jobId: String,
            token: String,
        ): ApiResult<LichessImportJobStatus> {
            getImportJobCalls += 1
            capturedGetImportJobId = jobId
            return jobStatusResponse
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
            // Production v2 path: neither v1 importGames nor v2 startImport
            // should fire when there is no linked account.
            assertEquals(0, client.importCalls)
            assertEquals(0, client.startImportCalls)
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
                // v2 path: startImport returns a terminal-succeeded
                // job synchronously (worker completed before the POST
                // returned, e.g. zero-game stream).  The ViewModel
                // skips the poll loop and transitions to Linked
                // directly via applyJobSnapshot.
                startImportResponse = ApiResult.Success(
                    LichessImportAccepted(
                        jobId = "job-abc",
                        status = LichessImportJobStatus.STATUS_SUCCEEDED,
                        inserted = 5,
                        skippedDuplicate = 0,
                        skippedInvalid = 0,
                        targetMaxGames = 25,
                    )
                )
            }
            val vm = newViewModel(client)
            vm.refreshStatus()
            vm.importGames(maxGames = 25)

            val state = vm.state as LichessConnectViewModel.UiState.Linked
            assertEquals(15, state.importedGameCount)  // 10 + 5
            assertNull("calibration banner should be cleared after import", state.calibration)
            assertEquals(25, client.capturedStartImportMaxGames)
            // v2 contract: lastImportedAt is NOT refreshed by the job
            // payload (it carries Unix ms, not the ISO string the UI
            // renders).  Stays at whatever priorLinked had (null here);
            // the next refreshStatus() call pulls the canonical value
            // from /lichess/status.
            assertNull(
                "lastImportedAt should not be set from the v2 job payload",
                state.lastImportedAt,
            )
            // Deprecated v1 client method MUST NOT be invoked by the
            // ViewModel — production now exclusively uses startImport.
            assertEquals(0, client.importCalls)
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

    // ─────────────────────────────────────────────────────────────────────
    // VM_IMPORT_v2_*  — async-import poll loop + dismiss + resume.
    //
    // VM_IMPORT_POLL_TRANSITIONS_TO_LINKED  startImport returns 'running';
    //                                        the polled getImportJob returns
    //                                        'succeeded'; state flips to
    //                                        Linked with merged counters.
    // VM_IMPORT_DISMISS_DOES_NOT_CANCEL_SERVER  pausePolling stops local
    //                                        polling without invoking
    //                                        unlink/DELETE.
    // VM_IMPORT_RESUME_ON_REOPEN              status response carrying
    //                                        activeImportJobId triggers an
    //                                        immediate getImportJob and
    //                                        transitions to Importing.
    // VM_IMPORT_LIFECYCLE_PAUSE              pausePolling + scheduler advance
    //                                        verifies the poll loop is
    //                                        genuinely cancelled, not just
    //                                        suspended.
    // ─────────────────────────────────────────────────────────────────────

    @Test
    fun `VM_IMPORT_POLL_TRANSITIONS_TO_LINKED - running then succeeded merges counts`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                statusResponse = ApiResult.Success(
                    LichessStatusResponse(
                        linked = true,
                        externalUsername = "thibault",
                        importedGameCount = 10,
                    )
                )
                // POST returns the job at 'running' (worker started but not done).
                startImportResponse = ApiResult.Success(
                    LichessImportAccepted(
                        jobId = "j-poll",
                        status = LichessImportJobStatus.STATUS_RUNNING,
                        inserted = 0,
                        targetMaxGames = 50,
                    )
                )
                // Polled GET returns terminal-succeeded with non-zero inserted.
                jobStatusResponse = ApiResult.Success(
                    LichessImportJobStatus(
                        jobId = "j-poll",
                        status = LichessImportJobStatus.STATUS_SUCCEEDED,
                        inserted = 7,
                        targetMaxGames = 50,
                    )
                )
            }
            val vm = newViewModel(client)
            vm.refreshStatus()
            vm.importGames(50)

            // After startImport returns 'running', state is Importing.
            val importing = vm.state as LichessConnectViewModel.UiState.Importing
            assertEquals("j-poll", importing.jobId)
            assertEquals(50, importing.target)

            // Advance virtual time past the poll interval; the poll coroutine
            // wakes from delay(), calls getImportJob, observes 'succeeded',
            // and transitions to Linked.
            scheduler.advanceTimeBy(LichessConnectViewModel.POLL_INTERVAL_MS + 100)
            scheduler.runCurrent()

            val linked = vm.state as LichessConnectViewModel.UiState.Linked
            assertEquals(17, linked.importedGameCount)  // 10 + 7
            assertNull(
                "calibration banner must be cleared after import",
                linked.calibration,
            )
            assertEquals(1, client.getImportJobCalls)
            assertEquals("j-poll", client.capturedGetImportJobId)
        }

    @Test
    fun `VM_IMPORT_DISMISS_DOES_NOT_CANCEL_SERVER - pausePolling stops polls without unlink`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                statusResponse = ApiResult.Success(
                    LichessStatusResponse(
                        linked = true,
                        externalUsername = "thibault",
                        importedGameCount = 0,
                    )
                )
                startImportResponse = ApiResult.Success(
                    LichessImportAccepted(
                        jobId = "j-dismiss",
                        status = LichessImportJobStatus.STATUS_RUNNING,
                        inserted = 5,
                        targetMaxGames = 50,
                    )
                )
                // If the poll loop fires, this returns running too — but the
                // test asserts it does NOT fire after pause.
                jobStatusResponse = ApiResult.Success(
                    LichessImportJobStatus(
                        jobId = "j-dismiss",
                        status = LichessImportJobStatus.STATUS_RUNNING,
                        inserted = 6,
                        targetMaxGames = 50,
                    )
                )
            }
            val vm = newViewModel(client)
            vm.refreshStatus()
            vm.importGames(50)
            assertTrue(vm.state is LichessConnectViewModel.UiState.Importing)
            val pollsBeforePause = client.getImportJobCalls

            // Simulate the Fragment onStop() pathway.
            vm.pausePolling()

            // Even after multiple poll intervals, no new GETs land.
            scheduler.advanceTimeBy(LichessConnectViewModel.POLL_INTERVAL_MS * 5)
            scheduler.runCurrent()

            assertEquals(
                "no new poll GETs after pausePolling",
                pollsBeforePause,
                client.getImportJobCalls,
            )
            // Critically: dismiss does NOT cancel the server-side job.
            // We assert this by checking we never invoked unlink (the
            // only API call that cancels server-side).
            assertEquals("dismiss must not invoke unlink", 0, client.unlinkCalls)
            // State stays in Importing — the row is still alive server-side.
            assertTrue(vm.state is LichessConnectViewModel.UiState.Importing)
        }

    @Test
    fun `VM_IMPORT_RESUME_ON_REOPEN - status with activeImportJobId rejoins Importing`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                // Status response carries the active job_id discovered server-side
                // (a prior import left running while the sheet was dismissed).
                statusResponse = ApiResult.Success(
                    LichessStatusResponse(
                        linked = true,
                        externalUsername = "thibault",
                        importedGameCount = 3,
                        activeImportJobId = "j-resume",
                    )
                )
                jobStatusResponse = ApiResult.Success(
                    LichessImportJobStatus(
                        jobId = "j-resume",
                        status = LichessImportJobStatus.STATUS_RUNNING,
                        inserted = 17,
                        targetMaxGames = 50,
                    )
                )
            }
            val vm = newViewModel(client)
            vm.refreshStatus()

            // refreshStatus saw active_import_job_id and immediately
            // resumed the Importing state without requiring a button tap.
            val state = vm.state as LichessConnectViewModel.UiState.Importing
            assertEquals("j-resume", state.jobId)
            assertEquals(17, state.inserted)
            assertEquals(50, state.target)
            // status + one immediate getImportJob — no poll yet.
            assertEquals(1, client.statusCalls)
            assertEquals(1, client.getImportJobCalls)
            // startImport must NOT have been invoked — we joined an
            // existing job, not started a new one.
            assertEquals(0, client.startImportCalls)

            // Cleanup: cancel the still-active poll coroutine so
            // runTest's leftover-tasks check doesn't hang waiting for
            // the next delay() to fire.
            vm.pausePolling()
        }

    @Test
    fun `VM_IMPORT_LIFECYCLE_PAUSE - resume after pause re-polls via refreshStatus`() =
        runTest(testDispatcher) {
            val client = FakeLichessClient().apply {
                statusResponse = ApiResult.Success(
                    LichessStatusResponse(
                        linked = true,
                        externalUsername = "thibault",
                        importedGameCount = 0,
                        activeImportJobId = null,
                    )
                )
                startImportResponse = ApiResult.Success(
                    LichessImportAccepted(
                        jobId = "j-lifecycle",
                        status = LichessImportJobStatus.STATUS_RUNNING,
                        inserted = 2,
                        targetMaxGames = 50,
                    )
                )
                jobStatusResponse = ApiResult.Success(
                    LichessImportJobStatus(
                        jobId = "j-lifecycle",
                        status = LichessImportJobStatus.STATUS_RUNNING,
                        inserted = 3,
                        targetMaxGames = 50,
                    )
                )
            }
            val vm = newViewModel(client)
            vm.refreshStatus()
            vm.importGames(50)
            val pollsBeforePause = client.getImportJobCalls

            // onStop equivalent.
            vm.pausePolling()
            scheduler.advanceTimeBy(LichessConnectViewModel.POLL_INTERVAL_MS * 10)
            scheduler.runCurrent()
            // Polling is genuinely cancelled — not just suspended.
            assertEquals(pollsBeforePause, client.getImportJobCalls)

            // onStart equivalent: re-fire refreshStatus, with status now
            // exposing the still-active job (server kept running).
            client.statusResponse = ApiResult.Success(
                LichessStatusResponse(
                    linked = true,
                    externalUsername = "thibault",
                    importedGameCount = 0,
                    activeImportJobId = "j-lifecycle",
                )
            )
            val statusCallsBeforeResume = client.statusCalls
            val pollsBeforeResume = client.getImportJobCalls

            vm.refreshStatus()

            // refreshStatus did a fresh status fetch + immediate getImportJob.
            assertTrue(client.statusCalls > statusCallsBeforeResume)
            assertTrue(client.getImportJobCalls > pollsBeforeResume)
            // State is back in Importing (resumed, not started fresh).
            assertTrue(vm.state is LichessConnectViewModel.UiState.Importing)

            // Cleanup: the resumed poll coroutine is now suspended at
            // delay().  Cancel it explicitly so runTest's leftover-tasks
            // assertion does not hang.
            vm.pausePolling()
        }
}
