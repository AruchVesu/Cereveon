import XCTest
@testable import Cereveon

private func decodeHistory(_ json: String) -> GameHistoryResponse {
    try! APIJSON.decode(GameHistoryResponse.self, from: Data(json.utf8))
}
private func decodePositions(_ json: String) -> GamePositionsResponse {
    try! APIJSON.decode(GamePositionsResponse.self, from: Data(json.utf8))
}

private final class FakeGameHistoryClient: GameHistoryClient {
    var historyResult: APIResult<GameHistoryResponse>
    var positionsResult: APIResult<GamePositionsResponse>

    init(history: APIResult<GameHistoryResponse> = .httpError(500),
         positions: APIResult<GamePositionsResponse> = .httpError(500)) {
        historyResult = history
        positionsResult = positions
    }

    func history(token: String) async -> APIResult<GameHistoryResponse> { historyResult }
    func positions(eventId: String, token: String) async -> APIResult<GamePositionsResponse> { positionsResult }
}

@MainActor
final class GameHistoryTests: XCTestCase {

    // MARK: - History list view-model

    private let historyJSON = """
    {"games": [
      {"id":"e1","game_id":"g1","last_move":"Qxf7","winner_move":"Qxf7","result":"win","created_at":"2026-06-22T10:00:00"},
      {"id":"e2","game_id":null,"last_move":"Kg1","winner_move":null,"result":"draw","created_at":"2026-06-21T09:00:00"}
    ]}
    """

    func testHistoryLoadMapsRows() async {
        let vm = GameHistoryViewModel(client: FakeGameHistoryClient(history: .success(decodeHistory(historyJSON))), token: { "t" })
        await vm.load()
        guard case let .loaded(rows) = vm.state else { return XCTFail("expected loaded: \(vm.state)") }
        XCTAssertEqual(rows.count, 2)
        XCTAssertEqual(rows[0].outcome, .win)
        XCTAssertEqual(rows[0].subtitle, "last Qxf7 · won Qxf7")
        XCTAssertEqual(rows[0].date, "Jun 22")
        XCTAssertEqual(rows[1].outcome, .draw)
        XCTAssertEqual(rows[1].subtitle, "last Kg1")   // no winner on a draw
    }

    func testHistoryEmptyAndErrorAndLoggedOut() async {
        let empty = GameHistoryViewModel(client: FakeGameHistoryClient(history: .success(decodeHistory(#"{"games":[]}"#))), token: { "t" })
        await empty.load()
        XCTAssertEqual(empty.state, .empty)

        let errored = GameHistoryViewModel(client: FakeGameHistoryClient(history: .httpError(500)), token: { "t" })
        await errored.load()
        XCTAssertEqual(errored.state, .error)

        let loggedOut = GameHistoryViewModel(client: FakeGameHistoryClient(), token: { nil })
        await loggedOut.load()
        XCTAssertEqual(loggedOut.state, .error)
    }

    func testOutcomeMapping() {
        XCTAssertEqual(GameHistoryViewModel.outcome("win"), .win)
        XCTAssertEqual(GameHistoryViewModel.outcome("LOSS"), .loss)
        XCTAssertEqual(GameHistoryViewModel.outcome("1/2-1/2"), .draw)
        XCTAssertEqual(GameHistoryViewModel.outcome("weird"), .other)
    }

    // MARK: - Replay view-model

    private let positionsJSON = """
    {"positions": [
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
      "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"
    ], "moves": ["e4","e5"]}
    """

    func testReplayOpensOnFinalAndStepsAndRenders() async {
        let vm = GameReplayViewModel(eventId: "e1",
                                     client: FakeGameHistoryClient(positions: .success(decodePositions(positionsJSON))),
                                     token: { "t" })
        await vm.load()
        XCTAssertEqual(vm.state, .ready)
        XCTAssertEqual(vm.plyCount, 2)
        XCTAssertEqual(vm.index, 2, "opens on the final position")
        XCTAssertFalse(vm.canForward)
        XCTAssertTrue(vm.canBack)
        XCTAssertTrue(vm.whiteToMove, "final position is White to move")

        let finalBoard = vm.board
        vm.stepBack()
        XCTAssertEqual(vm.index, 1)
        XCTAssertNotEqual(vm.board, finalBoard, "the board re-renders on step")
        XCTAssertFalse(vm.whiteToMove, "after 1.e4 it's Black to move")

        vm.goToStart()
        XCTAssertEqual(vm.index, 0)
        XCTAssertFalse(vm.canBack)
        XCTAssertTrue(vm.canForward)

        vm.goToEnd()
        XCTAssertEqual(vm.index, 2)
    }

    func testReplayMoveLabel() async {
        let vm = GameReplayViewModel(eventId: "e1",
                                     client: FakeGameHistoryClient(positions: .success(decodePositions(positionsJSON))),
                                     token: { "t" })
        await vm.load()
        vm.goToStart()
        XCTAssertEqual(vm.moveLabel, "Starting position")
        vm.stepForward()
        XCTAssertEqual(vm.moveLabel, "1. e4")
        vm.stepForward()
        XCTAssertEqual(vm.moveLabel, "1\u{2026} e5")   // 1… e5
    }

    func testReplayErrorOnEmptyPositions() async {
        let vm = GameReplayViewModel(eventId: "e1",
                                     client: FakeGameHistoryClient(positions: .success(decodePositions(#"{"positions":[],"moves":[]}"#))),
                                     token: { "t" })
        await vm.load()
        XCTAssertEqual(vm.state, .error)
    }
}
