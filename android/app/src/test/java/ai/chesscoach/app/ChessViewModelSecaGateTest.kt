package ai.chesscoach.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * ChessViewModel must respect the [SecaSafetyGate] when dispatching the
 * per-move live coaching call.
 *
 * The README contract: "before sending coaching requests, confirm
 * safe_mode=true".  Per-move /live/move is the dominant coaching
 * surface in normal play; this test pins the per-call check inside
 * [ChessViewModel.dispatchHumanMoveCoach].
 *
 * Stable test IDs (do NOT rename):
 *   VM_GATE_01  Gate Safe → live-coach call fires
 *   VM_GATE_02  Gate Unsafe → live-coach call skipped
 *   VM_GATE_03  Gate Unknown → live-coach call skipped (cold-start fail-closed)
 *   VM_GATE_04  Null gate (no wiring) → legacy unconditional behaviour preserved
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChessViewModelSecaGateTest {

    // Same scheduler pattern as ChessViewModelTest — explicit scheduler
    // avoids the Dispatchers.Main race when multiple test classes run
    // in the same JVM.
    private val scheduler = TestCoroutineScheduler()
    private val testDispatcher = StandardTestDispatcher(scheduler)

    private class FakeEngine : EngineProvider {
        override fun getBestMove(fen: String): AIMove = AIMove(0, 0, 1, 1)
    }

    private class RecordingLiveClient : LiveMoveClient {
        var callCount: Int = 0
            private set

        override suspend fun getLiveCoaching(
            fen: String,
            uci: String,
            playerId: String,
        ): ApiResult<LiveMoveResponse> {
            callCount++
            return ApiResult.Success(
                LiveMoveResponse(status = "ok", hint = "ok", moveQuality = "GOOD", mode = "LIVE_V1"),
            )
        }
    }

    /**
     * Minimal in-test gate.  Production wires [HttpSecaSafetyGate];
     * tests don't need the network round-trip — they just need to
     * pin a known state on the [SecaSafetyGate.state] flow that
     * [ChessViewModel.dispatchHumanMoveCoach] reads via [isSafe].
     */
    private class FakeGate(initial: SecaSafetyState) : SecaSafetyGate {
        private val _state = MutableStateFlow(initial)
        override val state: StateFlow<SecaSafetyState> = _state
        override suspend fun refresh() = Unit
    }

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun playMove(vm: ChessViewModel) {
        var fenCallCount = 0
        vm.onHumanMove(
            fr = 6, fc = 4, tr = 4, tc = 4,
            applyHumanMove = { MoveResult.SUCCESS },
            exportFEN = {
                fenCallCount++
                if (fenCallCount <= 2) {
                    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
                } else {
                    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
                }
            },
            applyAIMove = { _, _, _, _ -> '.' },
        )
        scheduler.advanceUntilIdle()
    }

    @Test
    fun vm_gate_01_safe_state_allows_live_coach_call() {
        val live = RecordingLiveClient()
        val vm = ChessViewModel(
            engineProvider = FakeEngine(),
            ioDispatcher = testDispatcher,
            liveCoachClient = live,
            secaSafetyGate = FakeGate(SecaSafetyState.Safe),
        )
        playMove(vm)
        assertEquals("Safe state must allow /live/move", 1, live.callCount)
    }

    @Test
    fun vm_gate_02_unsafe_state_skips_live_coach_call() {
        val live = RecordingLiveClient()
        val vm = ChessViewModel(
            engineProvider = FakeEngine(),
            ioDispatcher = testDispatcher,
            liveCoachClient = live,
            secaSafetyGate = FakeGate(SecaSafetyState.Unsafe("test reason")),
        )
        playMove(vm)
        assertEquals(
            "Unsafe state must refuse /live/move — that's the README contract",
            0,
            live.callCount,
        )
    }

    @Test
    fun vm_gate_03_unknown_state_skips_live_coach_call() {
        val live = RecordingLiveClient()
        val vm = ChessViewModel(
            engineProvider = FakeEngine(),
            ioDispatcher = testDispatcher,
            liveCoachClient = live,
            secaSafetyGate = FakeGate(SecaSafetyState.Unknown),
        )
        playMove(vm)
        assertEquals(
            "Unknown is the cold-start window before the first refresh; the gate " +
                "must fail closed so a coaching call cannot race the status check",
            0,
            live.callCount,
        )
    }

    @Test
    fun vm_gate_04_null_gate_preserves_legacy_unconditional_behaviour() {
        // Tests that don't care about the gate (the bulk of ChessViewModel*
        // suites, which predate the gate) leave it null and continue to
        // observe the unconditional /live/move dispatch.
        val live = RecordingLiveClient()
        val vm = ChessViewModel(
            engineProvider = FakeEngine(),
            ioDispatcher = testDispatcher,
            liveCoachClient = live,
            secaSafetyGate = null,
        )
        playMove(vm)
        assertEquals(
            "Null gate must behave exactly like the pre-gate code so existing " +
                "ChessViewModel tests don't need updates",
            1,
            live.callCount,
        )
    }
}
