import XCTest
@testable import Cereveon

private struct StubLiveCoach: LiveMoveClient {
    let response: LiveMoveResponse
    func liveCoaching(fen: String, uci: String, fenBefore: String?, token: String) async -> APIResult<LiveMoveResponse> {
        .success(response)
    }
}

/// Engine that never returns a move (keeps the dispatch tests focused on the
/// human-move coaching, not the AI reply).
private struct NoMoveEngine: EngineProvider {
    func bestMove(fen: String) -> AIMove? { nil }
    func bestMove(fen: String, strength: Int) -> AIMove? { nil }
    func perft(fen: String, depth: Int) -> UInt64 { 0 }
}

final class CoachWiringTests: XCTestCase {

    private func sq(_ s: String) -> Square {
        let file = Int(s.first!.asciiValue!) - 97
        let rank = Int(s.dropFirst())!
        return Square(row: 8 - rank, col: file)
    }

    @MainActor
    private func untilTrue(timeout: TimeInterval = 3, _ condition: () -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() && Date() < deadline {
            try? await Task.sleep(nanoseconds: 15_000_000)
        }
    }

    // MARK: - Pure mappings

    func testEvalBandFromCentipawns() {
        XCTAssertEqual(EvalBand.from(centipawns: nil), .equal)
        XCTAssertEqual(EvalBand.from(centipawns: 0), .equal)
        XCTAssertEqual(EvalBand.from(centipawns: 120), .better)
        XCTAssertEqual(EvalBand.from(centipawns: 400), .winning)
        XCTAssertEqual(EvalBand.from(centipawns: -120), .worse)
        XCTAssertEqual(EvalBand.from(centipawns: -400), .losing)
        XCTAssertEqual(EvalBand.from(centipawns: 10_000), .winning)   // mate
    }

    func testMoveQualityFromBackend() {
        XCTAssertEqual(MoveQuality(backend: "blunder"), .blunder)
        XCTAssertEqual(MoveQuality(backend: "MISTAKE"), .mistake)
        XCTAssertEqual(MoveQuality(backend: "GOOD"), .good)
        XCTAssertEqual(MoveQuality(backend: "best"), .good)
        XCTAssertNil(MoveQuality(backend: "unknown"))
        XCTAssertNil(MoveQuality(backend: ""))
    }

    // MARK: - Dispatch wiring

    @MainActor
    func testLiveCoachSetsHintAndQuality() async {
        let response = try! APIJSON.decode(
            LiveMoveResponse.self,
            from: Data(#"{"status":"ok","hint":"Watch that knight.","move_quality":"mistake","mode":"LIVE_V1"}"#.utf8)
        )
        let vm = PlayViewModel(
            engine: NoMoveEngine(),
            liveCoach: StubLiveCoach(response: response),
            token: { "tok" }
        )
        vm.onMove(from: sq("e2"), to: sq("e4"))
        await untilTrue { vm.coachHint != nil }
        XCTAssertEqual(vm.coachHint, "Watch that knight.")
        XCTAssertEqual(vm.moveQuality, .mistake)
    }
}
