import Foundation
import Combine

/// Drives the "sharpen" trainer: take a past game's player-to-move positions and,
/// for each, ask the player to find the engine's best move — judged by
/// POST /training/verify-replay. The human plays White in recorded games, so the
/// puzzle positions are the White-to-move ones. One attempt per position reveals
/// the engine's pick; the score is the count within threshold.
@MainActor
final class MistakeReplayViewModel: ObservableObject {
    enum State: Equatable {
        case loading
        case error
        case empty
        case ready
        case finished(correct: Int, total: Int)
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var board: [[Character]]
    @Published private(set) var whiteToMove = true
    @Published private(set) var bestFrom: Square?   // engine's best move, highlighted after an attempt
    @Published private(set) var bestTo: Square?
    @Published private(set) var feedback: String?
    @Published private(set) var index = 0
    @Published private(set) var correctCount = 0
    @Published private(set) var solved = false       // current position attempted → reveal + allow Next
    @Published private(set) var verifying = false

    private var queue: [String] = []
    private let game = ChessGame()
    private let eventId: String
    /// When set, these FENs are the queue directly (no history fetch, no filter) —
    /// used by the "Replay your mistake" CTA with the one biggest-mistake position.
    private let seedFENs: [String]?
    private let historyClient: GameHistoryClient
    private let verifyClient: VerifyReplayClient
    private let token: () -> String?
    private var verifyTask: Task<Void, Never>?

    var total: Int { queue.count }
    var isInteractive: Bool { state == .ready && !solved && !verifying }

    init(eventId: String,
         seedFENs: [String]? = nil,
         historyClient: GameHistoryClient,
         verifyClient: VerifyReplayClient,
         token: @escaping () -> String?) {
        self.eventId = eventId
        self.seedFENs = seedFENs
        self.historyClient = historyClient
        self.verifyClient = verifyClient
        self.token = token
        board = game.board
    }

    func load() async {
        state = .loading
        guard let token = token() else { state = .error; return }
        if let seedFENs, !seedFENs.isEmpty {
            queue = seedFENs
            index = 0
            correctCount = 0
            renderCurrent()
            state = .ready
            return
        }
        switch await historyClient.positions(eventId: eventId, token: token) {
        case let .success(response) where !response.positions.isEmpty:
            queue = Self.buildQueue(response.positions)
            guard !queue.isEmpty else { state = .empty; return }
            index = 0
            correctCount = 0
            renderCurrent()
            state = .ready
        case .success, .httpError, .timeout, .networkError:
            state = .error
        }
    }

    func attempt(from: Square, to: Square) {
        guard isInteractive, index < queue.count else { return }
        guard game.isLegal(from: from, to: to) else {
            feedback = "That isn't a legal move here."
            return
        }
        guard let token = token() else { return }
        let uci = uciString(from: from, to: to)
        let fen = queue[index]
        verifying = true
        feedback = nil
        verifyTask = Task {
            let result = await verifyClient.verify(fen: fen, moveUci: uci, token: token)
            self.verifying = false
            switch result {
            case let .success(verdict):
                self.applyVerdict(verdict)
            case .httpError, .timeout, .networkError:
                self.feedback = "Couldn't reach the engine. Try again."
            }
        }
    }

    func next() {
        guard solved else { return }
        index += 1
        if index >= queue.count {
            state = .finished(correct: correctCount, total: queue.count)
            return
        }
        solved = false
        feedback = nil
        bestFrom = nil
        bestTo = nil
        renderCurrent()
    }

    func awaitVerifyCompletion() async { await verifyTask?.value }

    private func applyVerdict(_ verdict: VerifyReplayResponse) {
        if let best = Self.squares(fromUCI: verdict.engineBestUci) {
            bestFrom = best.from
            bestTo = best.to
        }
        solved = true
        if verdict.isCorrect {
            correctCount += 1
            feedback = "Best move. ✓"
        } else {
            feedback = "Not quite — the engine plays \(verdict.engineBestUci) (−\(verdict.evalLossCp) cp)."
        }
    }

    private func renderCurrent() {
        game.load(fen: queue[index])
        board = game.board
        whiteToMove = game.whiteToMove
    }

    private func uciString(from: Square, to: Square) -> String {
        var uci = from.algebraic + to.algebraic
        let piece = game.piece(at: from)
        // Auto-queen a pawn reaching the last rank (rare in these positions).
        if (piece == "P" && to.row == 0) || (piece == "p" && to.row == 7) { uci += "q" }
        return uci
    }

    static func squares(fromUCI uci: String) -> (from: Square, to: Square)? {
        let chars = Array(uci)
        guard chars.count >= 4,
              let from = SANResolver.square(algebraic: String(chars[0...1])),
              let to = SANResolver.square(algebraic: String(chars[2...3]))
        else { return nil }
        return (from, to)
    }

    /// White-to-move positions (the player's), skipping the very opening and the
    /// final board, capped to keep a session short.
    static func buildQueue(_ positions: [String]) -> [String] {
        // Need at least one position in the [2, count-1) window; `count > 2`
        // keeps the range valid (count == 2 would form an invalid `2..<1`).
        guard positions.count > 2 else { return [] }
        var result: [String] = []
        for index in 2..<(positions.count - 1) where sideToMove(positions[index]) == "w" {
            result.append(positions[index])
        }
        return Array(result.prefix(12))
    }

    private static func sideToMove(_ fen: String) -> String {
        fen.split(separator: " ").dropFirst().first.map(String.init) ?? "w"
    }
}
