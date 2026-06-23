import XCTest
@testable import Cereveon

private func sq(_ algebraic: String) -> Square {
    SANResolver.square(algebraic: algebraic)!
}

private func game(_ fen: String? = nil) -> ChessGame {
    let g = ChessGame()
    if let fen { g.load(fen: fen) }
    return g
}

// MARK: - SAN resolver + line tokenizer

final class SANResolverTests: XCTestCase {

    func testPawnPushFromStart() {
        let move = SANResolver.resolve("e4", in: game())
        XCTAssertEqual(move?.from, sq("e2"))
        XCTAssertEqual(move?.to, sq("e4"))
    }

    func testKnightsFromStart() {
        XCTAssertEqual(SANResolver.resolve("Nf3", in: game())?.from, sq("g1"))
        XCTAssertEqual(SANResolver.resolve("Nc3", in: game())?.from, sq("b1"))
    }

    func testCastlingKingside() {
        let g = game("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
        let move = SANResolver.resolve("O-O", in: g)
        XCTAssertEqual(move?.from, sq("e1"))
        XCTAssertEqual(move?.to, sq("g1"))
    }

    func testRookFileDisambiguation() {
        // Two white rooks on a1 and d1 both reach c1 → file disambig is required.
        let g = game("6k1/8/8/8/8/8/8/R2R2K1 w - - 0 1")
        XCTAssertEqual(SANResolver.resolve("Rac1", in: g)?.from, sq("a1"))
        XCTAssertEqual(SANResolver.resolve("Rdc1", in: g)?.from, sq("d1"))
        XCTAssertNil(SANResolver.resolve("Rc1", in: g), "ambiguous without disambiguation → nil")
    }

    func testPawnCapture() {
        let g = game("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2")
        let move = SANResolver.resolve("exd5", in: g)
        XCTAssertEqual(move?.from, sq("e4"))
        XCTAssertEqual(move?.to, sq("d5"))
    }

    func testBlackToMove() {
        let g = game("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
        let move = SANResolver.resolve("c5", in: g)
        XCTAssertEqual(move?.from, sq("c7"))
        XCTAssertEqual(move?.to, sq("c5"))
    }

    func testStripsCheckSymbol() {
        XCTAssertEqual(SANResolver.resolve("Nf3+", in: game())?.from, sq("g1"))
    }

    func testUnresolvableReturnsNil() {
        XCTAssertNil(SANResolver.resolve("e5", in: game()), "e2-e5 is illegal")
        XCTAssertNil(SANResolver.resolve("zz", in: game()))
    }

    func testTokenizerGlyphsAndNumbers() {
        XCTAssertEqual(OpeningLine.sanMoves(from: "1.e4 e5 2.♘f3 ♘c6"), ["e4", "e5", "Nf3", "Nc6"])
        XCTAssertEqual(OpeningLine.sanMoves(from: "1. e4 c6 2. d4 d5"), ["e4", "c6", "d4", "d5"])
        XCTAssertEqual(OpeningLine.sanMoves(from: "1.e4 e5 1-0"), ["e4", "e5"])
    }
}

// MARK: - Drill view-model

@MainActor
final class OpeningDrillTests: XCTestCase {

    private func opening(line: String) -> RepertoireOpening {
        let json = #"{"eco":"X","name":"Test","line":"\#(line)","mastery":0.5,"is_active":true,"ordinal":0}"#
        return try! APIJSON.decode(RepertoireOpening.self, from: Data(json.utf8))
    }

    func testCorrectMovesAdvanceAndFinishPerfect() {
        var recorded: Double?
        let vm = OpeningDrillViewModel(opening: opening(line: "1.e4 e5 2.Nf3 Nc6")) { recorded = $0 }
        XCTAssertEqual(vm.state, .playing)
        XCTAssertEqual(vm.totalPlies, 4)

        vm.attempt(from: sq("e2"), to: sq("e4")); XCTAssertEqual(vm.ply, 1)
        vm.attempt(from: sq("e7"), to: sq("e5")); XCTAssertEqual(vm.ply, 2)
        vm.attempt(from: sq("g1"), to: sq("f3")); XCTAssertEqual(vm.ply, 3)
        vm.attempt(from: sq("b8"), to: sq("c6"))

        XCTAssertEqual(vm.state, .finished(outcome: 1.0, mistakes: 0))
        XCTAssertEqual(recorded, 1.0)
    }

    func testWrongMoveCountsMistakeAndDoesNotAdvance() {
        let vm = OpeningDrillViewModel(opening: opening(line: "1.e4 e5")) { _ in }
        vm.attempt(from: sq("d2"), to: sq("d4"))   // book move is e4
        XCTAssertEqual(vm.ply, 0)
        XCTAssertEqual(vm.mistakes, 1)
        XCTAssertNotNil(vm.feedback)

        vm.attempt(from: sq("e2"), to: sq("e4"))   // correct now
        XCTAssertEqual(vm.ply, 1)
        XCTAssertNil(vm.feedback)
    }

    func testRevealAdvancesAndLowersOutcome() {
        var recorded: Double?
        let vm = OpeningDrillViewModel(opening: opening(line: "1.e4")) { recorded = $0 }
        vm.reveal()
        XCTAssertEqual(vm.ply, 1)
        XCTAssertEqual(vm.mistakes, 1)
        guard case let .finished(outcome, mistakes) = vm.state else { return XCTFail("expected finished") }
        XCTAssertEqual(mistakes, 1)
        XCTAssertEqual(outcome, 0.8, accuracy: 0.0001)
        XCTAssertEqual(recorded ?? 0, 0.8, accuracy: 0.0001)
    }

    func testEmptyLineIsInvalid() {
        let vm = OpeningDrillViewModel(opening: opening(line: "")) { _ in }
        XCTAssertEqual(vm.state, .invalid)
    }
}
