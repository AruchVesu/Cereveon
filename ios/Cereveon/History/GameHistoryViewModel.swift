import Foundation
import Combine

/// Loads GET /game/history and maps it to display rows. UI-agnostic: the outcome
/// is an enum the view colours, not a Color.
@MainActor
final class GameHistoryViewModel: ObservableObject {
    enum Outcome { case win, loss, draw, other }

    struct Row: Identifiable, Equatable {
        let id: String          // event id (used to fetch replay positions)
        let outcome: Outcome
        let subtitle: String    // "last Qxf7 · won Qxf7"
        let date: String        // "Jun 22"
    }

    enum State: Equatable {
        case loading
        case loaded([Row])
        case empty
        case error
    }

    @Published private(set) var state: State = .loading

    private let client: GameHistoryClient
    private let token: () -> String?

    init(client: GameHistoryClient, token: @escaping () -> String?) {
        self.client = client
        self.token = token
    }

    func load() async {
        state = .loading
        guard let token = token() else { state = .error; return }
        switch await client.history(token: token) {
        case let .success(response):
            let rows = response.games.map(Self.row(from:))
            state = rows.isEmpty ? .empty : .loaded(rows)
        case .httpError, .timeout, .networkError:
            state = .error
        }
    }

    // MARK: - Pure mapping

    static func row(from item: GameHistoryItem) -> Row {
        Row(id: item.id,
            outcome: outcome(item.result),
            subtitle: subtitle(last: item.lastMove, winner: item.winnerMove),
            date: shortDate(item.createdAt))
    }

    static func outcome(_ result: String) -> Outcome {
        switch result.lowercased() {
        case "win", "1-0": return .win
        case "loss", "0-1": return .loss
        case "draw", "1/2-1/2", "½-½": return .draw
        default: return .other
        }
    }

    static func subtitle(last: String?, winner: String?) -> String {
        var parts: [String] = []
        if let last, !last.isEmpty { parts.append("last \(last)") }
        if let winner, !winner.isEmpty { parts.append("won \(winner)") }
        return parts.joined(separator: " · ")
    }

    static func shortDate(_ iso: String?) -> String {
        guard let iso, let datePart = iso.split(separator: "T").first else { return "" }
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: String(datePart)) else { return "" }
        let out = DateFormatter()
        out.locale = Locale(identifier: "en_US_POSIX")
        out.dateFormat = "MMM d"
        return out.string(from: date)
    }
}
