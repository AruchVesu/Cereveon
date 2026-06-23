import Foundation
import Combine

/// Drives the Lichess Connect screen: load status, link by username, run the
/// async import (POST → 202 job → poll until terminal), and unlink. Link/import
/// failures surface as a transient `banner` over the current phase rather than
/// replacing the whole screen; only an initial status-load failure is a full
/// `.error`.
@MainActor
final class LichessConnectViewModel: ObservableObject {
    enum Phase: Equatable {
        case loading
        case notLinked
        case linked(handle: String, gameCount: Int)
        case importing(inserted: Int, target: Int)
        case error(String)
    }

    @Published private(set) var phase: Phase = .loading
    @Published private(set) var banner: String?
    @Published private(set) var busy = false
    @Published var usernameDraft = ""

    private let client: LichessClient
    private let token: () -> String?
    private let pollIntervalNanos: UInt64
    private let maxPolls: Int
    private var importTask: Task<Void, Never>?

    init(client: LichessClient,
         token: @escaping () -> String?,
         pollIntervalNanos: UInt64 = 2_000_000_000,
         maxPolls: Int = 90) {
        self.client = client
        self.token = token
        self.pollIntervalNanos = pollIntervalNanos
        self.maxPolls = maxPolls
    }

    var canLink: Bool {
        !usernameDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !busy
    }

    var isImporting: Bool {
        if case .importing = phase { return true }
        return false
    }

    func load() async {
        phase = .loading
        guard let token = token() else { phase = .error("You're signed out."); return }
        switch await client.status(token: token) {
        case let .success(status):
            phase = status.linked
                ? .linked(handle: status.externalUsername ?? "—", gameCount: status.importedGameCount)
                : .notLinked
        case .httpError, .timeout, .networkError:
            phase = .error("Couldn't reach Lichess. Try again.")
        }
    }

    func link() async {
        let username = usernameDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !username.isEmpty, let token = token() else { return }
        busy = true
        banner = nil
        let result = await client.link(username: username, token: token)
        busy = false
        switch result {
        case .success:
            usernameDraft = ""
            await load()
        case let .httpError(code):
            banner = Self.linkError(code)   // stays .notLinked
        case .timeout, .networkError:
            banner = "Couldn't reach Lichess. Try again."
        }
    }

    func unlink() async {
        guard let token = token() else { return }
        busy = true
        banner = nil
        _ = await client.unlink(token: token)
        busy = false
        await load()
    }

    func startImport() {
        guard let token = token(), case .linked = phase else { return }
        importTask?.cancel()
        importTask = Task { await self.runImport(token: token) }
    }

    func awaitImportCompletion() async { await importTask?.value }

    private func runImport(token: String) async {
        banner = nil
        switch await client.importGames(maxGames: 50, token: token) {
        case let .success(job):
            await poll(job, token: token)
        case let .httpError(code):
            banner = "Import failed (error \(code))."
            await load()
        case .timeout, .networkError:
            banner = "Couldn't start the import. Try again."
            await load()
        }
    }

    private func poll(_ initial: LichessImportJob, token: String) async {
        var job = initial
        phase = .importing(inserted: job.inserted, target: job.targetMaxGames)
        var polls = 0
        while !job.isTerminal && polls < maxPolls {
            try? await Task.sleep(nanoseconds: pollIntervalNanos)
            if Task.isCancelled { return }
            polls += 1
            if case let .success(updated) = await client.importJob(jobId: job.jobId, token: token) {
                job = updated
                phase = .importing(inserted: job.inserted, target: job.targetMaxGames)
            }
        }
        if job.didFail { banner = "The import failed. Try again." }
        await load()   // refresh the linked count
    }

    private static func linkError(_ code: Int) -> String {
        switch code {
        case 400, 404, 422: return "Couldn't find that Lichess account."
        case 409: return "That Lichess account is already linked."
        case 429: return "Too many attempts. Wait a moment."
        case 503: return "Lichess is busy. Try again shortly."
        default: return "Couldn't link (error \(code))."
        }
    }
}
