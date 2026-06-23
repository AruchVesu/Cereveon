import Foundation
import Combine

/// Loads a finished game's per-ply positions (GET /game/{id}/positions) and steps
/// through them passively. Reuses `ChessGame.load(fen:)` to render each FEN with
/// the same board convention `ChessBoardView` expects.
@MainActor
final class GameReplayViewModel: ObservableObject {
    enum State: Equatable { case loading, ready, error }

    @Published private(set) var state: State = .loading
    @Published private(set) var board: [[Character]]
    @Published private(set) var whiteToMove = true
    @Published private(set) var index = 0

    private var positions: [String] = []
    private var moves: [String] = []
    private let game = ChessGame()
    private let client: GameHistoryClient
    private let eventId: String
    private let token: () -> String?

    init(eventId: String, client: GameHistoryClient, token: @escaping () -> String?) {
        self.eventId = eventId
        self.client = client
        self.token = token
        board = game.board
        whiteToMove = game.whiteToMove
    }

    var canBack: Bool { index > 0 }
    var canForward: Bool { index < positions.count - 1 }
    var plyCount: Int { max(positions.count - 1, 0) }

    /// Label for the move that produced the current position. Index 0 is the
    /// start; index i (≥1) shows the SAN of `moves[i-1]` with its move number.
    var moveLabel: String {
        guard index > 0, index - 1 < moves.count else { return "Starting position" }
        let san = moves[index - 1]
        let moveNumber = (index + 1) / 2
        let isWhiteMove = index % 2 == 1
        return "\(moveNumber)\(isWhiteMove ? "." : "…") \(san)"
    }

    func load() async {
        state = .loading
        guard let token = token() else { state = .error; return }
        switch await client.positions(eventId: eventId, token: token) {
        case let .success(response) where !response.positions.isEmpty:
            positions = response.positions
            moves = response.moves
            index = max(positions.count - 1, 0)   // open on the final position
            render()
            state = .ready
        case .success:
            state = .error   // empty positions → nothing to replay
        case .httpError, .timeout, .networkError:
            state = .error
        }
    }

    func stepForward() {
        guard canForward else { return }
        index += 1
        render()
    }

    func stepBack() {
        guard canBack else { return }
        index -= 1
        render()
    }

    func goToStart() {
        guard !positions.isEmpty else { return }
        index = 0
        render()
    }

    func goToEnd() {
        guard !positions.isEmpty else { return }
        index = positions.count - 1
        render()
    }

    private func render() {
        game.load(fen: positions[index])
        board = game.board
        whiteToMove = game.whiteToMove
    }
}
