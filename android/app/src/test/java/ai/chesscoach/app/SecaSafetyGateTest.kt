package ai.chesscoach.app

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for [HttpSecaSafetyGate] state transitions.
 *
 * Stable test IDs (do NOT rename):
 *   SSG_01  Initial state is Unknown
 *   SSG_02  Initial state fails closed (isSafe == false)
 *   SSG_03  Success(safe_mode=true) → Safe
 *   SSG_04  Success(safe_mode=false) → Unsafe with reason
 *   SSG_05  HttpError → Unsafe with HTTP code in reason
 *   SSG_06  Timeout → Unsafe with timeout reason
 *   SSG_07  NetworkError → Unsafe with network reason
 *   SSG_08  refresh() recovers Unsafe → Safe when backend flips back
 *   SSG_09  refresh() can transition Safe → Unsafe (backend drift mid-session)
 *   SSG_10  state.value reflects the latest refresh outcome (StateFlow contract)
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SecaSafetyGateTest {

    /**
     * Minimal stub that returns scripted [ApiResult]s for [getSecaStatus].
     * All other GameApiClient methods are unused in this test surface;
     * the interface's default 501 returns suffice.
     */
    private class StubClient(
        private val responses: ArrayDeque<ApiResult<SecaStatusDto>>,
    ) : GameApiClient {
        var callCount: Int = 0
            private set

        override suspend fun startGame(playerId: String) =
            ApiResult.HttpError(501)

        override suspend fun finishGame(req: GameFinishRequest) =
            ApiResult.HttpError(501)

        override suspend fun getSecaStatus(): ApiResult<SecaStatusDto> {
            callCount++
            return responses.removeFirst()
        }
    }

    private fun client(vararg results: ApiResult<SecaStatusDto>): StubClient =
        StubClient(ArrayDeque(results.toList()))

    @Test
    fun ssg_01_initial_state_is_unknown() {
        val gate = HttpSecaSafetyGate(client())
        assertEquals(SecaSafetyState.Unknown, gate.state.value)
    }

    @Test
    fun ssg_02_initial_state_fails_closed() {
        val gate = HttpSecaSafetyGate(client())
        assertFalse(
            "Unknown must not be treated as Safe — that would let a coaching " +
                "request through the cold-start window before the first refresh",
            gate.isSafe(),
        )
    }

    @Test
    fun ssg_03_success_safe_mode_true_yields_safe() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(client(ApiResult.Success(SecaStatusDto(safeModeEnabled = true))))
        gate.refresh()
        assertEquals(SecaSafetyState.Safe, gate.state.value)
        assertTrue(gate.isSafe())
    }

    @Test
    fun ssg_04_success_safe_mode_false_yields_unsafe_with_reason() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(client(ApiResult.Success(SecaStatusDto(safeModeEnabled = false))))
        gate.refresh()
        val state = gate.state.value
        assertTrue("got $state", state is SecaSafetyState.Unsafe)
        val reason = (state as SecaSafetyState.Unsafe).reason
        assertTrue("reason should mention safe_mode false: $reason", reason.contains("safe_mode=false"))
        assertFalse(gate.isSafe())
    }

    @Test
    fun ssg_05_http_error_yields_unsafe_with_code() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(client(ApiResult.HttpError(503)))
        gate.refresh()
        val state = gate.state.value
        assertTrue("got $state", state is SecaSafetyState.Unsafe)
        assertTrue(
            "reason should surface HTTP code: ${(state as SecaSafetyState.Unsafe).reason}",
            state.reason.contains("503"),
        )
    }

    @Test
    fun ssg_06_timeout_yields_unsafe() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(client(ApiResult.Timeout))
        gate.refresh()
        val state = gate.state.value
        assertTrue(state is SecaSafetyState.Unsafe)
        assertTrue(
            "reason should mention timeout: ${(state as SecaSafetyState.Unsafe).reason}",
            state.reason.lowercase().contains("timed out") ||
                state.reason.lowercase().contains("timeout"),
        )
    }

    @Test
    fun ssg_07_network_error_yields_unsafe() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(
            client(ApiResult.NetworkError(java.net.UnknownHostException("offline"))),
        )
        gate.refresh()
        val state = gate.state.value
        assertTrue(state is SecaSafetyState.Unsafe)
        assertNotNull((state as SecaSafetyState.Unsafe).reason)
    }

    @Test
    fun ssg_08_refresh_recovers_unsafe_to_safe() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(
            client(
                ApiResult.HttpError(503),                                  // first call: backend down
                ApiResult.Success(SecaStatusDto(safeModeEnabled = true)),  // second: backend back
            ),
        )
        gate.refresh()
        assertTrue(gate.state.value is SecaSafetyState.Unsafe)
        gate.refresh()
        assertEquals(SecaSafetyState.Safe, gate.state.value)
    }

    @Test
    fun ssg_09_refresh_can_transition_safe_to_unsafe() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(
            client(
                ApiResult.Success(SecaStatusDto(safeModeEnabled = true)),    // initially safe
                ApiResult.Success(SecaStatusDto(safeModeEnabled = false)),   // backend drifted
            ),
        )
        gate.refresh()
        assertEquals(SecaSafetyState.Safe, gate.state.value)
        gate.refresh()
        assertTrue(
            "drift mid-session must mark gate Unsafe so the next coaching " +
                "request is refused (and the Snackbar appears)",
            gate.state.value is SecaSafetyState.Unsafe,
        )
    }

    @Test
    fun ssg_10_state_flow_emits_each_transition() = runTest(UnconfinedTestDispatcher()) {
        val gate = HttpSecaSafetyGate(
            client(
                ApiResult.Success(SecaStatusDto(safeModeEnabled = true)),
                ApiResult.HttpError(500),
                ApiResult.Success(SecaStatusDto(safeModeEnabled = true)),
            ),
        )
        val seen = mutableListOf<SecaSafetyState>()
        val job = backgroundScope.launch { gate.state.collect { seen.add(it) } }
        gate.refresh()
        gate.refresh()
        gate.refresh()
        // UnconfinedTestDispatcher delivers each state transition synchronously
        // before refresh() returns, so the sequence is Unknown → Safe → Unsafe → Safe.
        job.cancel()
        assertEquals("transitions: $seen", 4, seen.size)
        assertEquals(SecaSafetyState.Unknown, seen[0])
        assertEquals(SecaSafetyState.Safe, seen[1])
        assertTrue(seen[2] is SecaSafetyState.Unsafe)
        assertEquals(SecaSafetyState.Safe, seen[3])
    }
}
