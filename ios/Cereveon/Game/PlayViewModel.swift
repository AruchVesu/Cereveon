import Foundation
import Combine

/// Drives a game of the human (White) vs the on-device engine (Black), and the
/// coaching layer around it: per-move `/live/move` hints, the `/engine/eval`
/// band, and `/game` start/finish persistence. Owns a `ChessGame`; runs the
/// engine reply off the main actor; a `generation` counter discards anything
/// (engine reply or coaching response) that arrives after a reset. Mirrors the
/// turn loop of Android's `ChessViewModel`.
@MainActor
final class PlayViewModel: ObservableObject {
    private enum Turn { case human, ai }

    @Published private(set) var board: [[Character]]
    @Published private(set) var whiteToMove: Bool
    @Published private(set) var lastMoveFrom: Square?
    @Published private(set) var lastMoveTo: Square?
    @Published private(set) var pendingPromotion: Square?
    @Published private(set) var gameResult: GameResult?
    @Published private(set) var aiThinking = false

    // Coaching state (Phase 2c-ii).
    @Published private(set) var coachHint: String?
    @Published private(set) var moveQuality: MoveQuality?
    @Published private(set) var evalBand: EvalBand = .equal

    var isHumanTurn: Bool {
        turn == .human && !aiThinking && gameResult == nil && pendingPromotion == nil
    }

    private let game = ChessGame()
    private let engine: EngineProvider
    private let aiStrength: Int

    // Coaching dependencies; nil disables that feature (tests / logged-out).
    private let liveCoach: LiveMoveClient?
    private let evalClient: EngineEvalClient?
    private let gameClient: GameClient?
    private let token: () -> String?

    private var turn: Turn = .human
    private var generation = 0
    private var moveHistory: [String] = []
    private var gameId: String?
    private var humanMoveFenBefore: String?

    init(engine: EngineProvider = NativeEngineProvider(),
         aiStrength: Int = 100,
         liveCoach: LiveMoveClient? = nil,
         evalClient: EngineEvalClient? = nil,
         gameClient: GameClient? = nil,
         token: @escaping () -> String? = { nil }) {
        self.engine = engine
        self.aiStrength = aiStrength
        self.liveCoach = liveCoach
        self.evalClient = evalClient
        self.gameClient = gameClient
        self.token = token
        board = game.board
        whiteToMove = game.whiteToMove
        startGameOnServer()
    }

    var uciHistory: [String] { moveHistory }

    // Live game state read by the coach chat panel (Phase 3b). The chat sends the
    // board the user currently sees, scoped to the active server game, and names
    // the last move in plain English.
    var currentFEN: String { game.exportFEN() }
    var activeGameId: String? { gameId }
    var lastMoveUci: String? { moveHistory.last }
    var halfMoveCount: Int { moveHistory.count }

    // MARK: - Intents

    func onMove(from: Square, to: Square) {
        guard isHumanTurn else { return }
        let fenBefore = game.exportFEN()
        switch game.move(from: from, to: to) {
        case .failed:
            return
        case .promotion:
            humanMoveFenBefore = fenBefore
            moveHistory.append(uci(from: from, to: to, promo: nil))
            sync()
            pendingPromotion = to
        case .success:
            let move = uci(from: from, to: to, promo: nil)
            moveHistory.append(move)
            sync()
            afterHumanMove(uci: move, fenBefore: fenBefore)
        }
    }

    func completePromotion(_ kind: Character) {
        guard let square = pendingPromotion else { return }
        let fenBefore = humanMoveFenBefore ?? game.exportFEN()
        humanMoveFenBefore = nil
        game.promote(at: square, to: kind)
        var move = ""
        if let last = moveHistory.popLast() {
            move = last + String(kind).lowercased()
            moveHistory.append(move)
        }
        pendingPromotion = nil
        sync()
        afterHumanMove(uci: move, fenBefore: fenBefore)
    }

    func newGame() {
        generation += 1
        game.reset()
        turn = .human
        moveHistory.removeAll()
        pendingPromotion = nil
        gameResult = nil
        aiThinking = false
        coachHint = nil
        moveQuality = nil
        evalBand = .equal
        gameId = nil
        humanMoveFenBefore = nil
        sync()
        startGameOnServer()
    }

    // MARK: - Turn loop

    private func afterHumanMove(uci: String, fenBefore: String) {
        let fenAfter = game.exportFEN()
        dispatchLiveCoach(fenAfter: fenAfter, uci: uci, fenBefore: fenBefore)
        if let result = game.consumePendingGameResult() {
            refreshEval(fen: fenAfter)
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
            let raw = await Task.detached(priority: .userInitiated) {
                engine.bestMove(fen: fen, strength: strength)
            }.value
            let move = raw.flatMap { EngineMoveBridge.normalize($0, fen: fen) }
            guard gen == self.generation else { return }
            self.applyAIMove(move)
            self.aiThinking = false
        }
    }

    private func applyAIMove(_ move: AIMove?) {
        guard turn == .ai else { return }
        turn = .human
        guard let move, game.applyAIMove(from: move.fromSquare, to: move.toSquare) != nil else {
            sync()
            return
        }
        moveHistory.append(uci(from: move.fromSquare, to: move.toSquare, promo: move.promotion))
        sync()
        let fenAfter = game.exportFEN()
        refreshEval(fen: fenAfter)
        if let result = game.consumePendingGameResult() { finish(result) }
    }

    private func finish(_ result: GameResult) {
        turn = .human
        aiThinking = false
        gameResult = result
        dispatchGameFinish(result)
    }

    // MARK: - Coaching dispatch (fire-and-forget, race-guarded)

    private func dispatchLiveCoach(fenAfter: String, uci: String, fenBefore: String) {
        guard let liveCoach, let token = token(), uci.count >= 4 else { return }
        let gen = generation
        Task {
            let result = await liveCoach.liveCoaching(fen: fenAfter, uci: uci, fenBefore: fenBefore, token: token)
            guard gen == self.generation, case let .success(resp) = result else { return }
            self.coachHint = resp.hint.isEmpty ? nil : resp.hint
            self.moveQuality = MoveQuality(backend: resp.moveQuality)
        }
    }

    private func refreshEval(fen: String) {
        guard let evalClient else { return }
        let gen = generation
        Task {
            let result = await evalClient.evaluate(fen: fen)
            guard gen == self.generation, case let .success(eval) = result else { return }
            self.evalBand = EvalBand.from(centipawns: eval.score)
        }
    }

    private func startGameOnServer() {
        guard let gameClient, let token = token() else { return }
        let gen = generation
        Task {
            let result = await gameClient.startGame(token: token)
            guard gen == self.generation, case let .success(resp) = result else { return }
            self.gameId = resp.gameId.isEmpty ? nil : resp.gameId
        }
    }

    private func dispatchGameFinish(_ result: GameResult) {
        guard let gameClient, let token = token() else { return }
        let request = GameFinishRequest(
            pgn: exportPGN(result),
            result: resultString(result),
            accuracy: 0.5,            // the server recomputes; this is a fallback
            gameId: gameId
        )
        Task { _ = await gameClient.finishGame(request, token: token) }
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

    private func resultString(_ result: GameResult) -> String {
        switch result {
        case .whiteWins: return "win"   // the human plays White
        case .blackWins: return "loss"
        case .draw: return "draw"
        }
    }

    /// PGN with the four headers the backend `/game/finish` validator requires
    /// (White = Player, Black = Engine). The Result tag drives winner-move
    /// surfacing in game history.
    private func exportPGN(_ result: GameResult) -> String {
        let tag: String
        switch result {
        case .whiteWins: tag = "1-0"
        case .blackWins: tag = "0-1"
        case .draw: tag = "1/2-1/2"
        }
        let moves = moveHistory.enumerated()
            .map { index, uci in index % 2 == 0 ? "\(index / 2 + 1). \(uci)" : uci }
            .joined(separator: " ")
        return """
        [Event "Cereveon Game"]
        [White "Player"]
        [Black "Engine"]
        [Result "\(tag)"]

        \(moves)
        """
    }
}
