import Foundation
import Combine

/// Drives a game of the human (White) vs the on-device engine (Black). Owns a
/// `ChessGame`, turns board taps into moves, runs the engine reply off the main
/// actor, and publishes the board + game state for the play screen. Mirrors the
/// turn loop of Android's `ChessViewModel` (a `generation` counter guards against
/// results arriving after a reset).
@MainActor
final class PlayViewModel: ObservableObject {
    private enum Turn { case human, ai }

    @Published private(set) var board: [[Character]]
    @Published private(set) var whiteToMove: Bool
    @Published private(set) var lastMoveFrom: Square?
    @Published private(set) var lastMoveTo: Square?
    /// Non-nil while a human pawn awaits promotion (the screen shows the picker).
    @Published private(set) var pendingPromotion: Square?
    /// Non-nil once the game has ended.
    @Published private(set) var gameResult: GameResult?
    @Published private(set) var aiThinking = false

    /// Whether the board should accept human input right now.
    var isHumanTurn: Bool { turn == .human && !aiThinking && gameResult == nil && pendingPromotion == nil }

    private let game = ChessGame()
    private let engine: EngineProvider
    private let aiStrength: Int
    private var turn: Turn = .human
    private var generation = 0
    private var moveHistory: [String] = []

    init(engine: EngineProvider = NativeEngineProvider(), aiStrength: Int = 100) {
        self.engine = engine
        self.aiStrength = aiStrength
        board = game.board
        whiteToMove = game.whiteToMove
    }

    /// UCI move list (human + engine), e.g. ["e2e4", "e7e5", …]. For PGN / persistence.
    var uciHistory: [String] { moveHistory }

    // MARK: - Intents

    /// The human tapped a from→to move on the board.
    func onMove(from: Square, to: Square) {
        guard isHumanTurn else { return }
        switch game.move(from: from, to: to) {
        case .failed:
            return
        case .promotion:
            moveHistory.append(uci(from: from, to: to, promo: nil))   // promo char appended on completion
            sync()
            pendingPromotion = to
        case .success:
            moveHistory.append(uci(from: from, to: to, promo: nil))
            sync()
            advanceAfterHumanMove()
        }
    }

    /// Complete a pending pawn promotion. `kind` is one of q/r/b/n (any case).
    func completePromotion(_ kind: Character) {
        guard let square = pendingPromotion else { return }
        game.promote(at: square, to: kind)
        if let last = moveHistory.popLast() {
            moveHistory.append(last + String(kind).lowercased())   // e.g. "e7e8" + "q"
        }
        pendingPromotion = nil
        sync()
        advanceAfterHumanMove()
    }

    func newGame() {
        generation += 1            // discard any in-flight engine reply
        game.reset()
        turn = .human
        moveHistory.removeAll()
        pendingPromotion = nil
        gameResult = nil
        aiThinking = false
        sync()
    }

    // MARK: - Turn loop

    private func advanceAfterHumanMove() {
        if let result = game.consumePendingGameResult() {
            finish(result)
        } else {
            turn = .ai
            triggerAI()
        }
    }

    private func triggerAI() {
        guard turn == .ai, gameResult == nil else { return }
        aiThinking = true
        let gen = generation
        let fen = game.exportFEN()
        let engine = self.engine
        let strength = self.aiStrength
        Task {
            // The search is slow (≈2.5s at full strength), so run it off the main
            // actor and hop back to apply the result.
            let raw = await Task.detached(priority: .userInitiated) {
                engine.bestMove(fen: fen, strength: strength)
            }.value
            let move = raw.flatMap { EngineMoveBridge.normalize($0, fen: fen) }
            guard gen == self.generation else { return }   // a reset happened mid-think
            self.applyAIMove(move)
            self.aiThinking = false
        }
    }

    private func applyAIMove(_ move: AIMove?) {
        guard turn == .ai else { return }
        turn = .human
        guard let move, game.applyAIMove(from: move.fromSquare, to: move.toSquare) != nil else {
            // Engine returned no legal move (or the coordinate normalisation
            // failed): hand the turn back to the human rather than freeze.
            sync()
            return
        }
        moveHistory.append(uci(from: move.fromSquare, to: move.toSquare, promo: move.promotion))
        sync()
        if let result = game.consumePendingGameResult() { finish(result) }
    }

    private func finish(_ result: GameResult) {
        turn = .human
        aiThinking = false
        gameResult = result
    }

    // MARK: - Helpers

    private func sync() {
        board = game.board
        whiteToMove = game.whiteToMove
        lastMoveFrom = game.lastMoveFrom
        lastMoveTo = game.lastMoveTo
    }

    private func uci(from: Square, to: Square, promo: Character?) -> String {
        let base = from.algebraic + to.algebraic
        return promo.map { base + String($0).lowercased() } ?? base
    }
}
