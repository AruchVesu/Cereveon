import XCTest
@testable import Cereveon

final class ChessGameTests: XCTestCase {

    /// "e2" -> Square(row: 6, col: 4).
    private func sq(_ s: String) -> Square {
        let file = Int(s.first!.asciiValue!) - 97            // 'a' = 97
        let rank = Int(s.dropFirst())!
        return Square(row: 8 - rank, col: file)
    }

    func testStartingPositionFEN() {
        XCTAssertEqual(ChessGame().exportFEN(),
                       "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    }

    func testFENAfterE4() {
        let game = ChessGame()
        XCTAssertEqual(game.move(from: sq("e2"), to: sq("e4")), .success)
        XCTAssertEqual(game.exportFEN(),
                       "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
    }

    func testIllegalPawnMoveRejected() {
        XCTAssertEqual(ChessGame().move(from: sq("e2"), to: sq("e5")), .failed)
    }

    func testPinnedPieceCannotExposeKing() {
        let game = ChessGame()
        game.load(fen: "4k3/4b3/8/8/8/8/8/4R1K1 b - - 0 1")  // black Be7 pinned by Re1
        XCTAssertEqual(game.move(from: sq("e7"), to: sq("d6")), .failed)
    }

    func testKingsideCastle() {
        let game = ChessGame()
        game.load(fen: "4k3/8/8/8/8/8/8/4K2R w - - 0 1")
        XCTAssertEqual(game.move(from: sq("e1"), to: sq("g1")), .success)
        XCTAssertEqual(game.piece(at: sq("g1")), "K")
        XCTAssertEqual(game.piece(at: sq("f1")), "R")
        XCTAssertEqual(game.piece(at: sq("h1")), ".")
    }

    func testCannotCastleThroughAttackedSquare() {
        let game = ChessGame()
        game.load(fen: "4k3/8/8/8/8/5r2/8/4K2R w - - 0 1")   // black Rf3 attacks f1
        XCTAssertEqual(game.move(from: sq("e1"), to: sq("g1")), .failed)
    }

    func testEnPassantCapture() {
        let game = ChessGame()
        game.load(fen: "4k3/3p4/8/4P3/8/8/8/4K3 b - - 0 1")
        XCTAssertEqual(game.move(from: sq("d7"), to: sq("d5")), .success)   // sets ep target d6
        XCTAssertEqual(game.move(from: sq("e5"), to: sq("d6")), .success)   // exd6 e.p.
        XCTAssertEqual(game.piece(at: sq("d5")), ".")                       // captured pawn removed
        XCTAssertEqual(game.piece(at: sq("d6")), "P")
    }

    func testPromotionFlow() {
        let game = ChessGame()
        game.load(fen: "4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
        XCTAssertEqual(game.move(from: sq("a7"), to: sq("a8")), .promotion)
        game.promote(at: sq("a8"), to: "q")
        XCTAssertEqual(game.piece(at: sq("a8")), "Q")
    }

    func testFoolsMate() {
        let game = ChessGame()
        XCTAssertEqual(game.move(from: sq("f2"), to: sq("f3")), .success)
        XCTAssertEqual(game.move(from: sq("e7"), to: sq("e5")), .success)
        XCTAssertEqual(game.move(from: sq("g2"), to: sq("g4")), .success)
        XCTAssertEqual(game.move(from: sq("d8"), to: sq("h4")), .success)   // Qh4#
        XCTAssertTrue(game.gameOver)
        XCTAssertEqual(game.consumePendingGameResult(), .blackWins)
    }

    func testStalemate() {
        let game = ChessGame()
        game.load(fen: "7k/8/5QK1/8/8/8/8/8 w - - 0 1")
        XCTAssertEqual(game.move(from: sq("f6"), to: sq("f7")), .success)   // Qf7 stalemates Black
        XCTAssertTrue(game.gameOver)
        XCTAssertEqual(game.consumePendingGameResult(), .draw)
    }

    func testApplyAIMoveRejectsWrongSide() {
        // White to move; a black-piece move must be rejected (nil).
        XCTAssertNil(ChessGame().applyAIMove(from: sq("e7"), to: sq("e5")))
    }

    func testUndoRestoresStartingPosition() {
        let game = ChessGame()
        let start = game.exportFEN()
        XCTAssertEqual(game.move(from: sq("e2"), to: sq("e4")), .success)
        XCTAssertEqual(game.undo(), true)
        XCTAssertEqual(game.exportFEN(), start)
    }
}
