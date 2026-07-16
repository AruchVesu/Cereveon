package ai.chesscoach.app

import android.os.SystemClock
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.fragment.app.FragmentFactory
import androidx.fragment.app.testing.FragmentScenario
import androidx.fragment.app.testing.launchFragment
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented walk test for [TodaysDrillBottomSheet] — the multi-move
 * study-plan drill on a LIVE fragment (real dialog, real coroutine
 * dispatch, real [ChessBoardView]), network-free via a fake
 * [GameApiClient] whose engine verdict is always "correct".
 *
 * The pure walk state machine is host-JVM tested
 * ([TodaysDrillBottomSheetTest]); this suite covers the runtime glue that
 * only exists on a device/emulator: the verify round-trip driving
 * [ChessBoardView.applyAIMove] for the scripted reply, the
 * exportFEN-based decision-point tracking, the status progression, and
 * the solve calls at the end of the line.
 *
 * Invariants pinned
 * -----------------
 *  1. WALK_STEP_ADVANCES     an engine-approved line move auto-plays the
 *                            scripted reply, flips the decision point back
 *                            to the solver, updates the walk status, and
 *                            re-enables the board.
 *  2. WALK_SOLVE_AT_LINE_END the final solver move triggers
 *                            /training/solve (source_type=mistake_replay,
 *                            plan-shaped source_ref) and
 *                            /coach/plan/puzzle/complete with the drilled
 *                            day_offset.
 *  3. SINGLE_MOVE_NO_WALK_COPY an empty solution line keeps the legacy
 *                            single-decision presentation (no depth
 *                            announcement in the status row).
 */
@RunWith(AndroidJUnit4::class)
class TodaysDrillWalkInstrumentedTest {

    private val startposFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    /**
     * Always-correct engine + recording sinks.  Only the three methods the
     * drill sheet calls are overridden; the interface's 501 defaults cover
     * the rest, and the two abstract game methods return 501 explicitly.
     */
    private class FakeClient : GameApiClient {
        @Volatile var verifyCount = 0
        @Volatile var lastVerifiedFen: String? = null
        @Volatile var solveSourceType: String? = null
        @Volatile var solveSourceRef: String? = null
        @Volatile var completedPlanId: String? = null
        @Volatile var completedDayOffset: Int? = null

        override suspend fun startGame(playerId: String): ApiResult<GameStartResponse> =
            ApiResult.HttpError(501)

        override suspend fun finishGame(req: GameFinishRequest): ApiResult<GameFinishResponse> =
            ApiResult.HttpError(501)

        override suspend fun verifyReplayMove(
            fen: String,
            moveUci: String,
        ): ApiResult<VerifyReplayResponse> {
            verifyCount += 1
            lastVerifiedFen = fen
            return ApiResult.Success(VerifyReplayResponse(isCorrect = true))
        }

        override suspend fun submitTrainingSolve(
            sourceType: String,
            sourceRef: String?,
        ): ApiResult<TrainingSolveResponse> {
            solveSourceType = sourceType
            solveSourceRef = sourceRef
            return ApiResult.Success(TrainingSolveResponse(xpAwarded = 10, trainingXp = 100))
        }

        override suspend fun completePlanPuzzle(
            planId: String,
            dayOffset: Int,
        ): ApiResult<CoachPlanResponse> {
            completedPlanId = planId
            completedDayOffset = dayOffset
            return ApiResult.Success(CoachPlanResponse(planId = planId))
        }
    }

    private fun launchDrill(
        fake: FakeClient,
        solutionLine: List<String>,
    ): FragmentScenario<TodaysDrillBottomSheet> {
        // Build the argument bundle through the production factory (keeps
        // the private ARG_* keys in one place), then hand it to
        // FragmentScenario as `fragmentArgs` so the harness OWNS the
        // arguments and re-applies them on any re-instantiation — the
        // instance-lambda form lost them intermittently under emulator
        // load (a null requireArguments() at onViewCreated).  The fake is
        // injected via a FragmentFactory installed on the FragmentManager,
        // so it survives re-creation too; the factory builds a fresh
        // fragment via the default no-arg constructor and only wires the
        // client (arguments come from `fragmentArgs`).
        val args = TodaysDrillBottomSheet.newInstance(
            planId = "p-test",
            dayOffset = 3,
            totalDays = 3,
            theme = "queen_safety",
            verdict = "",
            fen = startposFen,
            expectedMoveUci = "e2e4",
            solutionLineUci = solutionLine,
        ).requireArguments()

        val factory = object : FragmentFactory() {
            override fun instantiate(classLoader: ClassLoader, className: String): Fragment {
                val fragment = super.instantiate(classLoader, className)
                if (fragment is TodaysDrillBottomSheet) fragment.gameApiClient = fake
                return fragment
            }
        }

        return launchFragment<TodaysDrillBottomSheet>(
            fragmentArgs = args,
            themeResId = R.style.Theme_Cereveon_Atrium,
            factory = factory,
        )
    }

    /** Poll (main-thread reads via onFragment) until [condition] or timeout. */
    private fun awaitOnFragment(
        scenario: FragmentScenario<TodaysDrillBottomSheet>,
        what: String,
        timeoutMs: Long = 5_000,
        condition: (TodaysDrillBottomSheet) -> Boolean,
    ) {
        val deadline = SystemClock.uptimeMillis() + timeoutMs
        while (SystemClock.uptimeMillis() < deadline) {
            var met = false
            scenario.onFragment { fragment -> met = condition(fragment) }
            if (met) return
            SystemClock.sleep(100)
        }
        throw AssertionError("timed out waiting for: $what")
    }

    /** Poll the fake (instrumentation thread) until [condition] or timeout. */
    private fun awaitFake(what: String, timeoutMs: Long = 5_000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeoutMs
        while (SystemClock.uptimeMillis() < deadline) {
            if (condition()) return
            SystemClock.sleep(100)
        }
        throw AssertionError("timed out waiting for: $what")
    }

    private fun playMove(
        scenario: FragmentScenario<TodaysDrillBottomSheet>,
        fr: Int, fc: Int, tr: Int, tc: Int,
    ) = scenario.onFragment { fragment ->
        val board = fragment.requireView().findViewById<ChessBoardView>(R.id.todaysDrillBoard)
        board.onMovePlayed!!.invoke(fr, fc, tr, tc)
    }

    @Test
    fun walkStepAdvances_replyAutoPlaysAndStatusTicks() {
        val fake = FakeClient()
        val scenario = launchDrill(fake, listOf("e2e4", "e7e5", "g1f3"))
        try {
            // Depth is announced up front.
            awaitOnFragment(scenario, "walk intro status") { f ->
                f.requireView().findViewById<TextView>(R.id.todaysDrillStatus).text ==
                    TodaysDrillBottomSheet.formatWalkStatus(found = 0, total = 2)
            }

            playMove(scenario, 6, 4, 4, 4) // e2e4 — line move 1

            // Verify ran against the puzzle-start decision point, the
            // scripted reply e7e5 auto-played (White to move again), the
            // walk status ticked, and the board unlocked for move 2.
            awaitOnFragment(scenario, "step-1 walk status") { f ->
                f.requireView().findViewById<TextView>(R.id.todaysDrillStatus).text ==
                    TodaysDrillBottomSheet.formatWalkStatus(found = 1, total = 2)
            }
            assertEquals(1, fake.verifyCount)
            assertEquals(startposFen, fake.lastVerifiedFen)
            scenario.onFragment { f ->
                val board =
                    f.requireView().findViewById<ChessBoardView>(R.id.todaysDrillBoard)
                assertTrue(
                    "reply must hand the move back to the solver",
                    board.exportFEN().contains(" w "),
                )
                assertTrue("board must unlock for the next step", board.isInteractive)
            }
        } finally {
            scenario.close()
        }
    }

    @Test
    fun walkSolveAtLineEnd_creditsAndAdvancesThePlan() {
        val fake = FakeClient()
        val scenario = launchDrill(fake, listOf("e2e4", "e7e5", "g1f3"))
        try {
            playMove(scenario, 6, 4, 4, 4) // e2e4
            awaitFake("first verify") { fake.verifyCount == 1 }
            awaitOnFragment(scenario, "board unlocked mid-walk") { f ->
                f.requireView()
                    .findViewById<ChessBoardView>(R.id.todaysDrillBoard).isInteractive
            }

            playMove(scenario, 7, 6, 5, 5) // g1f3 — the line's last solver move

            // The solve flow may dismiss the dialog, so assert on the fake
            // from the instrumentation thread only.
            awaitFake("solve + plan-complete calls") {
                fake.solveSourceType != null && fake.completedDayOffset != null
            }
            assertEquals(2, fake.verifyCount)
            assertTrue(
                "mid-walk verify must use the post-reply decision point",
                fake.lastVerifiedFen!!.contains(" w ") &&
                    fake.lastVerifiedFen != startposFen,
            )
            assertEquals(
                MistakeReplayBottomSheet.SOURCE_TYPE_MISTAKE_REPLAY,
                fake.solveSourceType,
            )
            assertEquals(
                TodaysDrillBottomSheet.formatSourceRef(planId = "p-test", dayOffset = 3),
                fake.solveSourceRef,
            )
            assertEquals("p-test", fake.completedPlanId)
            assertEquals(3, fake.completedDayOffset)
        } finally {
            scenario.close()
        }
    }

    @Test
    fun singleMoveDrill_keepsLegacyPresentation() {
        val fake = FakeClient()
        val scenario = launchDrill(fake, emptyList())
        try {
            scenario.onFragment { f ->
                val status =
                    f.requireView().findViewById<TextView>(R.id.todaysDrillStatus).text.toString()
                assertTrue(
                    "no walk depth announcement for a single-decision drill, got: $status",
                    !status.startsWith("This one runs deeper"),
                )
            }
            // One engine-approved move solves immediately (empty line =
            // deviation-free single-decision flow).
            playMove(scenario, 6, 4, 4, 4)
            awaitFake("single-move solve") { fake.solveSourceType != null }
            assertEquals(1, fake.verifyCount)
            assertEquals(3, fake.completedDayOffset)
        } finally {
            scenario.close()
        }
    }
}
