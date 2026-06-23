import Foundation
import Combine

/// Drives the opening-repertoire screen: load, set-active, delete, add, and
/// record a drill outcome. Every editing call returns the full updated list, so
/// the VM just replaces its state from the response. Edit failures surface as a
/// transient `banner`.
@MainActor
final class OpeningsViewModel: ObservableObject {
    enum State: Equatable {
        case loading
        case loaded([RepertoireOpening])
        case error
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var banner: String?
    @Published private(set) var busy = false

    private let client: RepertoireClient
    private let token: () -> String?

    init(client: RepertoireClient, token: @escaping () -> String?) {
        self.client = client
        self.token = token
    }

    var openings: [RepertoireOpening] {
        if case let .loaded(list) = state { return list }
        return []
    }

    var activeOpening: RepertoireOpening? {
        openings.first { $0.isActive }
    }

    func load() async {
        state = .loading
        guard let token = token() else { state = .error; return }
        switch await client.getRepertoire(token: token) {
        case let .success(response): apply(response)
        case .httpError, .timeout, .networkError: state = .error
        }
    }

    func setActive(_ eco: String) async {
        await mutate { await self.client.setActive(eco: eco, token: $0) }
    }

    func delete(_ eco: String) async {
        await mutate { await self.client.deleteOpening(eco: eco, token: $0) }
    }

    func recordDrill(_ eco: String, outcome: Double) async {
        await mutate { await self.client.drillResult(eco: eco, outcome: outcome, token: $0) }
    }

    func add(eco: String, name: String, line: String) async {
        let e = eco.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let l = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !e.isEmpty, !n.isEmpty, !l.isEmpty else {
            banner = "ECO, name and line are all required."
            return
        }
        await mutate { await self.client.addOpening(eco: e, name: n, line: l, token: $0) }
    }

    private func mutate(_ call: (String) async -> APIResult<RepertoireResponse>) async {
        guard let token = token() else { return }
        busy = true
        banner = nil
        let result = await call(token)
        busy = false
        switch result {
        case let .success(response): apply(response)
        case let .httpError(code): banner = Self.errorMessage(code)
        case .timeout, .networkError: banner = "Couldn't reach the server. Try again."
        }
    }

    private func apply(_ response: RepertoireResponse) {
        state = .loaded(response.openings.sorted { $0.ordinal < $1.ordinal })
    }

    /// Average half-move depth across the lines (one ply per space-separated
    /// token; the "1." numbering tokens inflate this slightly, matching Android).
    static func avgDepth(_ openings: [RepertoireOpening]) -> Int {
        guard !openings.isEmpty else { return 0 }
        let total = openings.map { $0.line.split(separator: " ").count }.reduce(0, +)
        return Int((Double(total) / Double(openings.count)).rounded())
    }

    private static func errorMessage(_ code: Int) -> String {
        switch code {
        case 400, 422: return "Invalid opening — check the ECO format."
        case 404: return "That opening is already gone."
        case 429: return "Too many changes. Wait a moment."
        default: return "Couldn't update (error \(code))."
        }
    }
}
