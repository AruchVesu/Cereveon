import XCTest
@testable import Cereveon

/// Deterministic engine stub. Returns the same move regardless of position
/// (enough to drive a single AI reply in the turn-loop tests). Sendable because
/// it is invoked off the main actor.
private struct FakeEngine: EngineProvider {
    let move: AIMove?
    func bestMove(fen: String) -> AIMove? { move }
    func bestMove(fen: String, strength: Int) -> AIMove? { move }
    func perft(fen: String, depth: Int) -> UInt64 { 0 }
}

final class PlayViewModelTests: XCTestCase {

    private func sq(_ s: String) -> Square {
        let file = Int(s.first!.asciiValue!) - 97
        let rank = Int(s.dropFirst())!
        return Square(row: 8 - rank, col: file)
    }

    @MainActor
    private func untilTrue(timeout: TimeInterval = 3, _ condition: () -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() && Date() < deadline {
            try? await Task.sleep(nanoseconds: 15_000_000)   // 15 ms
        }
    }

    @MainActor
    func testHumanMoveTriggersAIReply() async {
        // Engine (Black) replies e7-e5: e7 = (row 1, col 4), e5 = (row 3, col 4).
        // Zero think-pacing: this test awaits the reply in real time and
        // pins the turn loop, not the pacing (tested separately below).
        let fake = FakeEngine(move: AIMove(fromX: 1, fromY: 4, toX: 3, toY: 4, promotion: nil))
        let vm = PlayViewModel(engine: fake, aiThinkPacingNanos: { 0 })

        vm.onMove(from: sq("e2"), to: sq("e4"))
        await untilTrue { vm.uciHistory.count == 2 }

        XCTAssertEqual(vm.uciHistory, ["e2e4", "e7e5"])
        XCTAssertTrue(vm.whiteToMove)        // back to the human
        XCTAssertFalse(vm.aiThinking)
        XCTAssertNil(vm.gameResult)
    }

    @MainActor
    func testIllegalHumanMoveIgnored() {
        let vm = PlayViewModel(engine: FakeEngine(move: nil))
        vm.onMove(from: sq("e2"), to: sq("e5"))   // 3-square pawn move — illegal
        XCTAssertTrue(vm.uciHistory.isEmpty)
        XCTAssertFalse(vm.aiThinking)
    }

    @MainActor
    func testNewGameResets() async {
        let fake = FakeEngine(move: AIMove(fromX: 1, fromY: 4, toX: 3, toY: 4, promotion: nil))
        let vm = PlayViewModel(engine: fake, aiThinkPacingNanos: { 0 })   // zero pacing — real-time wait
        vm.onMove(from: sq("e2"), to: sq("e4"))
        await untilTrue { vm.uciHistory.count == 2 }

        vm.newGame()
        XCTAssertTrue(vm.uciHistory.isEmpty)
        XCTAssertNil(vm.gameResult)
        XCTAssertTrue(vm.whiteToMove)
        XCTAssertEqual(vm.board[6][4], "P")   // e2 pawn restored to its home square
    }

    @MainActor
    func testEngineReplyIsHeldForThePacingWindow() async {
        // 0.3s test window (the production 2–3s sample would slow the suite);
        // the lower-bound assert can't flake on slow CI — Task.sleep waits at
        // LEAST its duration, and a late poll only inflates `elapsed`.
        let fake = FakeEngine(move: AIMove(fromX: 1, fromY: 4, toX: 3, toY: 4, promotion: nil))
        let vm = PlayViewModel(engine: fake, aiThinkPacingNanos: { 300_000_000 })

        let start = Date()
        vm.onMove(from: sq("e2"), to: sq("e4"))
        await untilTrue { vm.uciHistory.count == 2 }

        XCTAssertEqual(vm.uciHistory.count, 2, "the held reply must still land")
        XCTAssertGreaterThanOrEqual(
            Date().timeIntervalSince(start), 0.3,
            "the engine reply must be held for the pacing window before landing"
        )
    }

    @MainActor
    func testAIThinkPacingWindowIs2To3Seconds() {
        // Product requirement, mirrored from Android's
        // ChessViewModel.AI_THINK_PACING_MIN/MAX_MS — keep in lock-step.
        XCTAssertEqual(PlayViewModel.aiThinkPacingMinNanos, 2_000_000_000)
        XCTAssertEqual(PlayViewModel.aiThinkPacingMaxNanos, 3_000_000_000)
    }

    @MainActor
    func testResumeReplaysSnapshot() {
        let snapshot = GameSnapshot(uciHistory: ["e2e4", "e7e5"], fen: "x",
                                    moveCount: 2, gameNumber: 3, savedAt: Date())
        let defaults = UserDefaults(suiteName: "PlayVMResume-\(UUID().uuidString)")!
        let vm = PlayViewModel(engine: FakeEngine(move: nil), resume: snapshot, snapshotDefaults: defaults)

        XCTAssertEqual(vm.uciHistory, ["e2e4", "e7e5"], "the move list is restored")
        XCTAssertEqual(vm.halfMoveCount, 2)
        XCTAssertTrue(vm.whiteToMove, "White to move after 1.e4 e5")
        XCTAssertEqual(vm.board[6][4], ".", "e2 vacated by the replayed move")
        XCTAssertEqual(vm.board[4][4], "P", "white pawn now on e4")
    }
}
