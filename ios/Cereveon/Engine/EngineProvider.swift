// Swift facade over the native engine. Mirrors the semantics of
// android/app/src/main/java/ai/chesscoach/app/EngineProvider.kt — return nil
// when the engine yields no move.
import Foundation

struct AIMove: Equatable {
    let fromX: Int
    let fromY: Int
    let toX: Int
    let toY: Int
    let promotion: Character?
}

protocol EngineProvider: Sendable {
    func bestMove(fen: String) -> AIMove?
    func bestMove(fen: String, strength: Int) -> AIMove?
    func perft(fen: String, depth: Int) -> UInt64
}

/// Production engine backed by the native SachmatuLenta via `CereveonEngine`.
///
/// Coordinate note: `AIMove` carries the engine's *raw* (Black-relative,
/// row/col) coordinates. Mapping those onto on-screen squares — the job
/// `EngineProvider.kt`'s `JniMoveBridge` does on Android — is deferred to the
/// Phase-2 board work. Phase 0 only verifies the engine reproduces Android's
/// search/perft.
/// `@unchecked Sendable`: stateless and thread-safe — each call constructs a
/// fresh `SachmatuLenta` on the stack, so the engine can be invoked off the main
/// actor (the search is too slow to block the UI).
final class NativeEngineProvider: EngineProvider, @unchecked Sendable {
    private let engine = CereveonEngine()

    func bestMove(fen: String) -> AIMove? {
        engine.bestMove(forFEN: fen).map(Self.convert)
    }

    func bestMove(fen: String, strength: Int) -> AIMove? {
        engine.bestMove(forFEN: fen, strength: strength).map(Self.convert)
    }

    func perft(fen: String, depth: Int) -> UInt64 {
        engine.perft(forFEN: fen, depth: depth)
    }

    private static func convert(_ m: CRVAIMove) -> AIMove {
        let promotion: Character? = m.promotion == 0
            ? nil
            : UnicodeScalar(UInt32(m.promotion)).map(Character.init)
        return AIMove(fromX: m.fromX, fromY: m.fromY,
                      toX: m.toX, toY: m.toY, promotion: promotion)
    }
}
