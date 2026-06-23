import Foundation
import Combine

/// Loads POST /curriculum/next — the next recommended study focus — and exposes
/// it for the Lessons card. Read-only: the curriculum is a recommender, not a
/// solvable puzzle source (see CurriculumNext).
@MainActor
final class LessonsViewModel: ObservableObject {
    enum State: Equatable {
        case loading
        case loaded(CurriculumNext)
        case error
    }

    @Published private(set) var state: State = .loading

    private let client: CurriculumClient
    private let token: () -> String?

    init(client: CurriculumClient, token: @escaping () -> String?) {
        self.client = client
        self.token = token
    }

    func load() async {
        state = .loading
        guard let token = token() else { state = .error; return }
        switch await client.next(token: token) {
        case let .success(plan): state = .loaded(plan)
        case .httpError, .timeout, .networkError: state = .error
        }
    }

    /// "tactical_vision" → "Tactical Vision". Empty → "—".
    static func humanize(_ raw: String) -> String {
        let parts = raw.split(whereSeparator: { $0 == "_" || $0 == " " })
        guard !parts.isEmpty else { return "—" }
        return parts.map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
    }
}
