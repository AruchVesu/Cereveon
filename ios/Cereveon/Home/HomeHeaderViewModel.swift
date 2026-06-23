import Foundation
import Combine

/// Backs the Home header cosmetics: persists the first-seen date for the "Day N"
/// kicker and loads the training-XP kicker from `/auth/me` (via a provider so the
/// auth client stays encapsulated in AuthViewModel). The avatar initials are pure
/// (HomeHeader.initials) and don't need this.
@MainActor
final class HomeHeaderViewModel: ObservableObject {
    @Published private(set) var xpKicker: String?

    private let firstSeen: Date
    private static let firstSeenKey = "cereveon.home_first_seen"

    init(defaults: UserDefaults = .standard, now: Date = Date()) {
        if let stored = defaults.object(forKey: Self.firstSeenKey) as? Date {
            firstSeen = stored
        } else {
            firstSeen = now
            defaults.set(now, forKey: Self.firstSeenKey)
        }
    }

    func dateKicker(now: Date = Date()) -> String {
        HomeHeader.dateKicker(now: now, firstSeen: firstSeen)
    }

    /// Load the XP kicker. `provider` returns the player's training XP (nil on
    /// failure / logged out) — typically `{ await auth.trainingXP() }`.
    func loadXP(_ provider: () async -> Int?) async {
        if let xp = await provider() {
            xpKicker = HomeHeader.xpKicker(xp: xp)
        }
    }
}
