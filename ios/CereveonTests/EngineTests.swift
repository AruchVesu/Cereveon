// Verifies the iOS build reproduces the Android SachmatuLenta engine.
//
// perft counts mirror engine/perft_test.cpp; the tactic mirrors
// engine/strength_test.cpp. Only Black-to-move cases are used for `bestMove`,
// because the bridge (CereveonEngine, like native_chess_engine.cpp) computes
// Black's move and ignores the FEN side-to-move. perft is side-agnostic (it uses
// the position's own side-to-move), so all three perft positions apply.
import XCTest
@testable import Cereveon

final class EngineTests: XCTestCase {

    private let engine = NativeEngineProvider()

    // MARK: - perft (engine/perft_test.cpp)

    func testPerftStartingPosition() {
        let fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        XCTAssertEqual(engine.perft(fen: fen, depth: 1), 20)
        XCTAssertEqual(engine.perft(fen: fen, depth: 2), 400)
        XCTAssertEqual(engine.perft(fen: fen, depth: 3), 8902)
        XCTAssertEqual(engine.perft(fen: fen, depth: 4), 197_281)
    }

    func testPerftKiwipete() {
        let fen = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq -"
        XCTAssertEqual(engine.perft(fen: fen, depth: 1), 48)
        XCTAssertEqual(engine.perft(fen: fen, depth: 2), 2039)
        XCTAssertEqual(engine.perft(fen: fen, depth: 3), 97_862)
    }

    func testPerftPosition3() {
        let fen = "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"
        XCTAssertEqual(engine.perft(fen: fen, depth: 1), 14)
        XCTAssertEqual(engine.perft(fen: fen, depth: 2), 191)
        XCTAssertEqual(engine.perft(fen: fen, depth: 3), 2812)
        XCTAssertEqual(engine.perft(fen: fen, depth: 4), 43_238)
    }

    // MARK: - bestMove (engine/strength_test.cpp — Black-to-move cases)

    /// strength_test.cpp:testHangingQueenCapture — Black rook a5 takes the white
    /// queen h5. Raw engine coords: (row 3, col 0) → (row 3, col 7).
    func testCapturesHangingQueenAtFullStrength() {
        let fen = "8/8/8/r6Q/8/8/8/k6K b - - 0 1"
        guard let m = engine.bestMove(fen: fen, strength: 100) else {
            return XCTFail("engine returned no move for the hanging-queen position")
        }
        XCTAssertEqual(m.fromX, 3)
        XCTAssertEqual(m.fromY, 0)
        XCTAssertEqual(m.toX, 3)
        XCTAssertEqual(m.toY, 7)
    }

    /// The no-strength overload (engine default = 100) must also find the capture.
    func testDefaultBestMoveFindsCapture() {
        let fen = "8/8/8/r6Q/8/8/8/k6K b - - 0 1"
        XCTAssertEqual(engine.bestMove(fen: fen),
                       AIMove(fromX: 3, fromY: 0, toX: 3, toY: 7, promotion: nil))
    }

    /// strength_test.cpp:testAlwaysValidMove — a legal Black move at every level.
    func testReturnsValidMoveAtAllStrengths() {
        let fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
        for strength in [0, 25, 50, 75, 100] {
            XCTAssertNotNil(engine.bestMove(fen: fen, strength: strength),
                            "no move returned at strength \(strength)")
        }
    }
}
