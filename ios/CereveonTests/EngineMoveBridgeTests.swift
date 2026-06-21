import XCTest
@testable import Cereveon

final class EngineMoveBridgeTests: XCTestCase {

    private let blackToMoveStart = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"

    func testIdentityWhenAlreadyLegal() {
        // Nb8-c6: b8 = (row 0, col 1), c6 = (row 2, col 2).
        let move = AIMove(fromX: 0, fromY: 1, toX: 2, toY: 2, promotion: nil)
        XCTAssertEqual(EngineMoveBridge.normalize(move, fen: blackToMoveStart), move)
    }

    func testInvalidMoveReturnsNil() {
        let invalid = AIMove(fromX: -1, fromY: -1, toX: -1, toY: -1, promotion: nil)
        XCTAssertNil(EngineMoveBridge.normalize(invalid, fen: blackToMoveStart))
    }

    func testUnparseableFenPassesMoveThrough() {
        let move = AIMove(fromX: 0, fromY: 1, toX: 2, toY: 2, promotion: nil)
        XCTAssertEqual(EngineMoveBridge.normalize(move, fen: "not-a-fen"), move)
    }

    func testFindsRowFlippedTransform() {
        // Identity is b1-c3 (a White move on Black's turn — illegal). The row-flip
        // is Nb8-c6, which is legal; normalize must discover it.
        let move = AIMove(fromX: 7, fromY: 1, toX: 5, toY: 2, promotion: nil)
        XCTAssertEqual(EngineMoveBridge.normalize(move, fen: blackToMoveStart),
                       AIMove(fromX: 0, fromY: 1, toX: 2, toY: 2, promotion: nil))
    }
}
