import Foundation

/// Pure formatters for the Home header cosmetics, mirroring Android's
/// `HomeActivity` companion helpers (`initialsFor` / `formatDateKicker` /
/// `formatXpKicker`). Kept framework-free so they're unit-testable.
enum HomeHeader {
    private static let placeholder = "\u{2014}" // —

    /// Up-to-2-letter initials from a player id. nil/blank/"demo" → "—". The auth
    /// layer surfaces only the JWT player id (no display name), so initials are
    /// derived from it (matches Android).
    static func initials(_ playerId: String?) -> String {
        guard let id = playerId?.trimmingCharacters(in: .whitespaces),
              !id.isEmpty, id.lowercased() != "demo"
        else { return placeholder }
        let alnum = Array(id.filter { $0.isLetter || $0.isNumber })
        guard let first = alnum.first else { return placeholder }
        let a = first.uppercased()
        let b = alnum.count >= 2 ? alnum[1].uppercased() : a
        return a + b
    }

    /// "<Weekday> · Day NNN" — N = whole 24h periods since `firstSeen`, floored at
    /// 1 so a same-day visit reads "Day 001" (matches Android's TimeUnit.toDays).
    static func dateKicker(now: Date, firstSeen: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.dateFormat = "EEEE"
        let weekday = formatter.string(from: now)
        let deltaDays = Int(now.timeIntervalSince(firstSeen) / 86_400)
        let dayN = max(1, deltaDays + 1)
        return "\(weekday) · Day \(String(format: "%03d", dayN))"
    }

    /// "Level N · X XP" — linear 100 XP/level (matches Android's formatXpKicker).
    static func xpKicker(xp: Int) -> String {
        let safe = max(0, xp)
        let level = max(1, safe / 100 + 1)
        return "Level \(level) · \(safe) XP"
    }
}
