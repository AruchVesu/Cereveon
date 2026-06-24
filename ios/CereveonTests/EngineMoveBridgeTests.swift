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

    // Castling / en passant: the engine emits these as bare king / pawn moves
    // with no special flag.  Before the bridge recognised their shapes,
    // normalize() returned nil and the engine silently skipped its reply.

    func testKeepsBlackKingsideCastle() {
        // Black king e8 -> g8, rook h8, f8/g8 empty.
        let move = AIMove(fromX: 0, fromY: 4, toX: 0, toY: 6, promotion: nil)
        XCTAssertEqual(EngineMoveBridge.normalize(move, fen: "4k2r/8/8/8/8/8/8/4K3 b k - 0 1"), move)
    }

    func testKeepsBlackQueensideCastle() {
        // Black king e8 -> c8, rook a8, b8/c8/d8 empty.
        let move = AIMove(fromX: 0, fromY: 4, toX: 0, toY: 2, promotion: nil)
        XCTAssertEqual(EngineMoveBridge.normalize(move, fen: "r3k3/8/8/8/8/8/8/4K3 b q - 0 1"), move)
    }

    func testKeepsBlackEnPassant() {
        // Black d4 pawn captures a white e4 pawn that just double-stepped; EP
        // target e3.  Diagonal move onto an empty square.
        let move = AIMove(fromX: 4, fromY: 3, toX: 5, toY: 4, promotion: nil)
        XCTAssertEqual(EngineMoveBridge.normalize(move, fen: "4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1"), move)
    }

    func testRejectsTwoSquareKingMoveWithNoRook() {
        // King "castles" with no rook on the corner — not a real castle, and no
        // transform is a legal 1-square king move.
        let move = AIMove(fromX: 0, fromY: 4, toX: 0, toY: 6, promotion: nil)
        XCTAssertNil(EngineMoveBridge.normalize(move, fen: "4k3/8/8/8/8/8/8/4K3 b - - 0 1"))
    }
}
