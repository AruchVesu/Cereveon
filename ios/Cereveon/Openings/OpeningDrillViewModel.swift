import Foundation
import Combine

/// Drives an on-board drill of an opening line: the player reproduces the book
/// moves (both sides, in order) on the board; each tap-move is validated against
/// the expected SAN via `SANResolver`. Mistakes lower the recorded outcome.
@MainActor
final class OpeningDrillViewModel: ObservableObject {
    enum State: Equatable {
        case playing
        case finished(outcome: Double, mistakes: Int)
        case invalid   // the stored line couldn't be parsed/resolved
    }

    @Published private(set) var board: [[Character]]
    @Published private(set) var whiteToMove: Bool
    @Published private(set) var lastFrom: Square?
    @Published private(set) var lastTo: Square?
    @Published private(set) var state: State
    @Published private(set) var ply = 0
    @Published private(set) var mistakes = 0
    @Published private(set) var feedback: String?

    let opening: RepertoireOpening
    private let sans: [String]
    private let game = ChessGame()
    private let onComplete: (Double) -> Void

    var totalPlies: Int { sans.count }

    init(opening: RepertoireOpening, onComplete: @escaping (Double) -> Void) {
        self.opening = opening
        self.onComplete = onComplete
        sans = OpeningLine.sanMoves(from: opening.line)
        board = game.board
        whiteToMove = game.whiteToMove
        state = sans.isEmpty ? .invalid : .playing
    }

    /// The player attempted `from`→`to`. Advances on the book move; otherwise
    /// counts a mistake and asks them to retry.
    func attempt(from: Square, to: Square, promotion: Character? = nil) {
        guard state == .playing, ply < sans.count else { return }
        guard let expected = SANResolver.resolve(sans[ply], in: game) else { state = .invalid; return }
        if from == expected.from, to == expected.to {
            feedback = nil
            apply(expected)
            advance()
        } else {
            mistakes += 1
            feedback = "Not the book move — try again."
        }
    }

    /// Reveal + play the book move (counts as a missed rep).
    func reveal() {
        guard state == .playing, ply < sans.count,
              let expected = SANResolver.resolve(sans[ply], in: game) else { return }
        mistakes += 1
        feedback = nil
        apply(expected)
        advance()
    }

    private func advance() {
        ply += 1
        if ply >= sans.count { finish() }
    }

    private func apply(_ move: (from: Square, to: Square, promotion: Character?)) {
        switch game.move(from: move.from, to: move.to) {
        case .promotion: game.promote(at: move.to, to: move.promotion ?? "Q")
        case .success, .failed: break
        }
        lastFrom = move.from
        lastTo = move.to
        board = game.board
        whiteToMove = game.whiteToMove
    }

    private func finish() {
        let outcome = mistakes == 0 ? 1.0 : max(0.2, 1.0 - Double(mistakes) * 0.2)
        state = .finished(outcome: outcome, mistakes: mistakes)
        onComplete(outcome)
    }
}
