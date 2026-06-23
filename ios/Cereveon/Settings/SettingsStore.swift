import Foundation
import Combine

/// Persisted user preferences, mirroring the Android `SettingsBottomSheet`
/// SharedPreferences store. Each property writes through to `UserDefaults` on
/// change. `coachVoice` shares its key with the chat panel's own picker
/// ([[ChatViewModel]]), so the two surfaces edit one underlying value.
///
/// Consumer wiring (matches Android):
///   • coachVoice  — read by ChatViewModel; sent as `coach_voice` each chat turn.
///   • boardStyle  — read by PlayView via `SettingsStore.boardStyle()`; selects
///                   the ChessBoardView render.
///   • sound / notifications — persisted ahead of the features that will read
///     them (no audio/notification system yet), exactly as on Android.
@MainActor
final class SettingsStore: ObservableObject {
    @Published var coachVoice: CoachVoice {
        didSet { defaults.set(coachVoice.rawValue, forKey: Keys.coachVoice) }
    }
    @Published var boardStyle: BoardStyle {
        didSet { defaults.set(boardStyle.rawValue, forKey: Keys.boardStyle) }
    }
    @Published var soundEnabled: Bool {
        didSet { defaults.set(soundEnabled, forKey: Keys.sound) }
    }
    @Published var notificationsEnabled: Bool {
        didSet { defaults.set(notificationsEnabled, forKey: Keys.notifications) }
    }

    private let defaults: UserDefaults

    enum Keys {
        /// Shared verbatim with `ChatViewModel` so the chat-panel voice picker and
        /// the Settings voice radio edit the same value.
        static let coachVoice = "cereveon.coach_voice"
        static let boardStyle = "cereveon.board_style"
        static let sound = "cereveon.sound_enabled"
        static let notifications = "cereveon.notifications_enabled"
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        // didSet does not fire during init, so these restores never write back.
        coachVoice = defaults.string(forKey: Keys.coachVoice)
            .flatMap(CoachVoice.init(rawValue:)) ?? .conversational
        boardStyle = defaults.string(forKey: Keys.boardStyle)
            .flatMap(BoardStyle.init(rawValue:)) ?? .flat
        soundEnabled = defaults.object(forKey: Keys.sound) as? Bool ?? true
        notificationsEnabled = defaults.object(forKey: Keys.notifications) as? Bool ?? true
    }

    /// Non-observing read for the play loop (PlayView selects the board render at
    /// presentation time). `nonisolated` + UserDefaults-only so it's callable from
    /// a View's property initializer.
    nonisolated static func boardStyle(_ defaults: UserDefaults = .standard) -> BoardStyle {
        defaults.string(forKey: Keys.boardStyle).flatMap(BoardStyle.init(rawValue:)) ?? .flat
    }
}

extension BoardStyle {
    /// Title-case label for the Settings radio.
    var label: String {
        switch self {
        case .flat: return "Flat"
        case .engraved: return "Engraved"
        case .wireframe: return "Wireframe"
        }
    }
}
