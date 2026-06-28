import XCTest
@testable import Cereveon

private func square(_ algebraic: String) -> Square {
    SANResolver.square(algebraic: algebraic)!
}

private func decodePositions(_ json: String) -> GamePositionsResponse {
    try! APIJSON.decode(GamePositionsResponse.self, from: Data(json.utf8))
}

private func verdict(correct: Bool, best: String, loss: Int = 0) -> VerifyReplayResponse {
    let json = #"{"is_correct":\#(correct),"engine_best_uci":"\#(best)","eval_loss_cp":\#(loss)}"#
    return try! APIJSON.decode(VerifyReplayResponse.self, from: Data(json.utf8))
}

private final class FakeHistoryClient: GameHistoryClient {
    let positionsResult: APIResult<GamePositionsResponse>
    init(_ positionsResult: APIResult<GamePositionsResponse>) { self.positionsResult = positionsResult }
    func history(token: String) async -> APIResult<GameHistoryResponse> { .httpError(500) }
    func positions(eventId: String, token: String) async -> APIResult<GamePositionsResponse> { positionsResult }
}

private final class FakeVerifyClient: VerifyReplayClient {
    var result: APIResult<VerifyReplayResponse>
    private(set) var lastFen: String?
    private(set) var lastUci: String?
    init(_ result: APIResult<VerifyReplayResponse>) { self.result = result }
    func verify(fen: String, moveUci: String, token: String) async -> APIResult<VerifyReplayResponse> {
        lastFen = fen; lastUci = moveUci
        return result
    }
}

// 1.e4 e5 2.Nf3 Nc6 3.Bb5 — six positions; White (player) to move at indices 0,2,4.
private let positionsJSON = """
{"positions":[
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
  "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
  "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
  "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
  "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
], "moves": ["e4","e5","Nf3","Nc6","Bb5"]}
"""

@MainActor
final class MistakeReplayTests: XCTestCase {

    private func makeVM(history: APIResult<GamePositionsResponse>,
                        verify: APIResult<VerifyReplayResponse> = .httpError(500),
                        token: String? = "t") -> (MistakeReplayViewModel, FakeVerifyClient) {
        let verifyClient = FakeVerifyClient(verify)
        let vm = MistakeReplayViewModel(eventId: "e1",
                                        historyClient: FakeHistoryClient(history),
                                        verifyClient: verifyClient,
                                        token: { token })
        return (vm, verifyClient)
    }

    // MARK: - Queue building (pure)

    func testBuildQueueKeepsWhiteToMoveSkippingOpeningAndLast() {
        let positions = decodePositions(positionsJSON).positions
        let queue = MistakeReplayViewModel.buildQueue(positions)
        // Indices 2 and 4 are White-to-move and in range [2, count-1); index 0
        // (opening) and the final position (5) are excluded.
        XCTAssertEqual(queue.count, 2)
        XCTAssertEqual(queue[0], positions[2])
        XCTAssertEqual(queue[1], positions[4])
    }

    func testSquaresFromUCI() {
        let move = MistakeReplayViewModel.squares(fromUCI: "g1f3")
        XCTAssertEqual(move?.from, square("g1"))
        XCTAssertEqual(move?.to, square("f3"))
        XCTAssertNil(MistakeReplayViewModel.squares(fromUCI: "zz"))
    }

    // MARK: - Load

    func testLoadReadyBuildsQueue() async {
        let (vm, _) = makeVM(history: .success(decodePositions(positionsJSON)))
        await vm.load()
        XCTAssertEqual(vm.state, .ready)
        XCTAssertGreaterThan(vm.total, 0)
        XCTAssertEqual(vm.index, 0)
        XCTAssertTrue(vm.whiteToMove)
    }

    func testLoadErrorAndLoggedOutAndEmpty() async {
        let (errored, _) = makeVM(history: .httpError(500))
        await errored.load()
        XCTAssertEqual(errored.state, .error)

        let (loggedOut, _) = makeVM(history: .success(decodePositions(positionsJSON)), token: nil)
        await loggedOut.load()
        XCTAssertEqual(loggedOut.state, .error)

        let (empty, _) = makeVM(history: .success(decodePositions(#"{"positions":["8/8/8/8/8/8/8/8 w - - 0 1"],"moves":[]}"#)))
        await empty.load()
        XCTAssertEqual(empty.state, .empty)
    }

    // MARK: - Attempt

    func testCorrectAttemptScoresAndHighlightsBest() async {
        let (vm, fake) = makeVM(history: .success(decodePositions(positionsJSON)),
                                verify: .success(verdict(correct: true, best: "g1f3")))
        await vm.load()
        vm.attempt(from: square("g1"), to: square("f3"))   // Nf3, a legal move here
        await vm.awaitVerifyCompletion()

        XCTAssertEqual(fake.lastUci, "g1f3")
        XCTAssertEqual(vm.correctCount, 1)
        XCTAssertTrue(vm.solved)
        XCTAssertEqual(vm.bestFrom, square("g1"))
        XCTAssertEqual(vm.bestTo, square("f3"))
        XCTAssertEqual(vm.feedback, "Best move. \u{2713}")
    }

    func testIncorrectAttemptRevealsBest() async {
        let (vm, _) = makeVM(history: .success(decodePositions(positionsJSON)),
                             verify: .success(verdict(correct: false, best: "g1f3", loss: 80)))
        await vm.load()
        vm.attempt(from: square("d2"), to: square("d4"))   // legal, but not "best" per the fake
        await vm.awaitVerifyCompletion()

        XCTAssertEqual(vm.correctCount, 0)
        XCTAssertTrue(vm.solved)
        XCTAssertEqual(vm.bestFrom, square("g1"))
        XCTAssertNotNil(vm.feedback)
    }

    func testIllegalMoveIsRejectedWithoutVerifying() async {
        let (vm, fake) = makeVM(history: .success(decodePositions(positionsJSON)),
                                verify: .success(verdict(correct: true, best: "g1f3")))
        await vm.load()
        vm.attempt(from: square("e2"), to: square("e4"))   // e2 is empty in this position
        await vm.awaitVerifyCompletion()

        XCTAssertNil(fake.lastUci, "no verify call for an illegal move")
        XCTAssertFalse(vm.solved)
        XCTAssertNotNil(vm.feedback)
    }

    func testNextAdvancesToFinish() async {
        // A 4-position game → exactly one White-to-move puzzle (index 2), so the
        // single Nf3 move stays legal across the (one-position) queue.
        let shortGame = #"""
        {"positions":[
          "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
          "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
          "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
          "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
        ], "moves": ["e4","e5","Nf3"]}
        """#
        let (vm, _) = makeVM(history: .success(decodePositions(shortGame)),
                             verify: .success(verdict(correct: true, best: "g1f3")))
        await vm.load()
        XCTAssertEqual(vm.total, 1)

        vm.attempt(from: square("g1"), to: square("f3"))
        await vm.awaitVerifyCompletion()
        XCTAssertTrue(vm.solved)
        vm.next()
        XCTAssertEqual(vm.state, .finished(correct: 1, total: 1))
    }

    func testSeedModeUsesGivenPositionsWithoutHistory() async {
        // A failing history client proves the seed bypasses the fetch.
        let vm = MistakeReplayViewModel(
            eventId: "",
            seedFENs: ["rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"],
            historyClient: FakeHistoryClient(.httpError(500)),
            verifyClient: FakeVerifyClient(.success(verdict(correct: true, best: "g1f3"))),
            token: { "t" }
        )
        await vm.load()
        XCTAssertEqual(vm.state, .ready, "seed mode skips the history fetch")
        XCTAssertEqual(vm.total, 1)
        XCTAssertTrue(vm.whiteToMove)
    }

    // MARK: - onSolved (study-plan "today's drill" advance hook)

    func testCorrectSolveFiresOnSolved() async {
        var solvedFired = 0
        let vm = MistakeReplayViewModel(
            eventId: "",
            seedFENs: ["rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"],
            historyClient: FakeHistoryClient(.httpError(500)),
            verifyClient: FakeVerifyClient(.success(verdict(correct: true, best: "g1f3"))),
            token: { "t" },
            onSolved: { solvedFired += 1 }
        )
        await vm.load()
        vm.attempt(from: square("g1"), to: square("f3"))   // Nf3, judged correct
        await vm.awaitVerifyCompletion()
        XCTAssertEqual(solvedFired, 1, "a verified-correct solve fires onSolved once")
    }

    func testWrongAttemptDoesNotFireOnSolved() async {
        var solvedFired = 0
        let vm = MistakeReplayViewModel(
            eventId: "",
            seedFENs: ["rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"],
            historyClient: FakeHistoryClient(.httpError(500)),
            verifyClient: FakeVerifyClient(.success(verdict(correct: false, best: "g1f3", loss: 90))),
            token: { "t" },
            onSolved: { solvedFired += 1 }
        )
        await vm.load()
        vm.attempt(from: square("d2"), to: square("d4"))   // legal, judged wrong
        await vm.awaitVerifyCompletion()
        XCTAssertEqual(solvedFired, 0, "a wrong attempt does not advance the plan")
    }
}
