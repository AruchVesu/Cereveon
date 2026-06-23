import Foundation

/// A lightweight snapshot of an in-progress local game, persisted after each move
/// so the Home "Resume" card can offer to continue it. Mirrors Android's
/// MainActivity `PREF_LAST_GAME_*` keys.
struct GameSnapshot: Codable, Equatable {
    let uciHistory: [String]   // full move list (UCI) — replayed to restore state
    let fen: String            // current position (consistency reference)
    let moveCount: Int
    let gameNumber: Int
    let savedAt: Date

    /// "Game NNN · move M" (mirrors Android's formatResumeTitle).
    var resumeTitle: String {
        "Game \(String(format: "%03d", max(1, gameNumber))) · move \(moveCount)"
    }

    /// "vs. adaptive · HH:mm" — the opponent reads as the adaptive engine (the Elo
    /// is hidden); the time is the wall-clock save time.
    var resumeSubtitle: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.dateFormat = "HH:mm"
        return "vs. adaptive · \(formatter.string(from: savedAt))"
    }
}

/// UserDefaults-backed persistence for the in-progress game.
enum GameSnapshotStore {
    private static let snapshotKey = "cereveon.in_progress_game"
    private static let counterKey = "cereveon.next_game_number"

    /// Snapshots older than this are treated as stale (the local AI + server
    /// session have long since lapsed). 6h matches Android's RESUME_TTL.
    static let ttl: TimeInterval = 6 * 60 * 60

    static func save(_ snapshot: GameSnapshot, defaults: UserDefaults = .standard) {
        guard let data = try? JSONEncoder().encode(snapshot) else { return }
        defaults.set(data, forKey: snapshotKey)
    }

    static func load(defaults: UserDefaults = .standard) -> GameSnapshot? {
        guard let data = defaults.data(forKey: snapshotKey) else { return nil }
        return try? JSONDecoder().decode(GameSnapshot.self, from: data)
    }

    static func clear(defaults: UserDefaults = .standard) {
        defaults.removeObject(forKey: snapshotKey)
    }

    /// The persisted snapshot iff it's still resumable: at least one move and
    /// within the TTL. nil otherwise (and a stale snapshot is left in place; the
    /// next save overwrites it).
    static func resumable(now: Date = Date(), defaults: UserDefaults = .standard) -> GameSnapshot? {
        guard let snapshot = load(defaults: defaults),
              snapshot.moveCount > 0,
              now.timeIntervalSince(snapshot.savedAt) <= ttl
        else { return nil }
        return snapshot
    }

    /// Monotonic game counter — incremented and returned for each new game so the
    /// Resume card can read "Game NNN".
    static func nextGameNumber(defaults: UserDefaults = .standard) -> Int {
        let next = defaults.integer(forKey: counterKey) + 1
        defaults.set(next, forKey: counterKey)
        return next
    }
}
