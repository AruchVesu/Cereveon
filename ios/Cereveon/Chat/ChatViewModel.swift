import Foundation
import Combine

/// Coach tone, sent as `coach_voice` (it shapes tone, not content). The raw
/// values are the backend allow-list verbatim ("conversational" / "formal" /
/// "terse"); the boundary validator 422s anything else, so these must match.
enum CoachVoice: String, CaseIterable, Identifiable {
    case conversational
    case formal
    case terse

    var id: String { rawValue }
    var wireValue: String { rawValue }

    /// Short Atrium label for the picker.
    var label: String {
        switch self {
        case .conversational: return "Warm"
        case .formal: return "Formal"
        case .terse: return "Terse"
        }
    }
}

/// Drives the coach chat panel: seeds from the server-authoritative history once
/// per open, sends a turn, and streams the reply into a growing assistant bubble.
/// Reads the live board / game / last-move through closures so it always sends
/// the position the user currently sees (mirrors Android's `ChatBottomSheet`,
/// which builds the request from the live board at send time).
@MainActor
final class ChatViewModel: ObservableObject {
    /// A single rendered chat message. `text` is mutable so a streaming assistant
    /// bubble can grow in place as chunks arrive.
    struct Message: Identifiable, Equatable {
        let id: UUID
        let role: Role
        var text: String

        enum Role { case user, assistant }
    }

    @Published private(set) var messages: [Message] = []
    @Published private(set) var isStreaming = false
    @Published private(set) var historyLoaded = false
    @Published var draft = ""

    /// Coach tone; persisted across sessions and sent as `coach_voice` each turn.
    @Published var coachVoice: CoachVoice {
        didSet { userDefaults.set(coachVoice.rawValue, forKey: Self.voiceDefaultsKey) }
    }

    /// Shown when a stream produced no content (offline / aborted-empty), so the
    /// panel never leaves an empty assistant bubble.
    static let offlineFallback = "The coach is offline right now — try again in a moment."

    /// Server limits the replayed history (≤ 50 turns, ≤ 2000 chars each); conform
    /// to them so a long conversation can't 422 the whole turn.
    private static let maxWireTurns = 50
    private static let maxWireChars = 2000

    private let client: ChatClient
    private let fen: () -> String
    private let gameId: () -> String?
    private let lastMove: () -> String?
    private let moveCount: () -> Int?
    private let token: () -> String?
    private let userDefaults: UserDefaults
    private static let voiceDefaultsKey = "cereveon.coach_voice"

    private var streamTask: Task<Void, Never>?

    init(client: ChatClient,
         fen: @escaping () -> String,
         gameId: @escaping () -> String? = { nil },
         lastMove: @escaping () -> String? = { nil },
         moveCount: @escaping () -> Int? = { nil },
         token: @escaping () -> String?,
         userDefaults: UserDefaults = .standard) {
        self.client = client
        self.fen = fen
        self.gameId = gameId
        self.lastMove = lastMove
        self.moveCount = moveCount
        self.token = token
        self.userDefaults = userDefaults
        // Restore the persisted voice (didSet doesn't fire during init, so this
        // read never writes back). Default to conversational.
        let stored = userDefaults.string(forKey: Self.voiceDefaultsKey)
        coachVoice = stored.flatMap(CoachVoice.init(rawValue:)) ?? .conversational
    }

    var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !isStreaming
            && token() != nil
    }

    /// Seed the panel from `GET /chat/history` once. The server is the source of
    /// truth for chat history; this is a UI cache. A second call is a no-op.
    func preloadHistory() async {
        guard !historyLoaded, let token = token() else { return }
        let result = await client.history(limit: 50, gameId: gameId(), token: token)
        if case let .success(response) = result, messages.isEmpty {
            messages = response.turns.compactMap(Self.message(from:))
        }
        historyLoaded = true
    }

    /// Send the current draft and stream the reply. Appends the user message and
    /// an empty assistant bubble, then grows the bubble as `.chunk`s arrive;
    /// `.abort` replaces any partial with the deterministic fallback; an empty
    /// result (offline / no content) shows `offlineFallback`.
    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming, let token = token() else { return }
        draft = ""
        messages.append(Message(id: UUID(), role: .user, text: text))

        // The server validates the whole replayed history each turn, so send the
        // full conversation (most-recent last), conformed to the server limits.
        // Built BEFORE the empty assistant placeholder, which is the streaming
        // target and not part of the wire history.
        let wire = messages.suffix(Self.maxWireTurns).map {
            ChatMessageDTO(role: $0.role == .user ? "user" : "assistant",
                           content: String($0.text.prefix(Self.maxWireChars)))
        }
        let assistantId = UUID()
        messages.append(Message(id: assistantId, role: .assistant, text: ""))
        isStreaming = true

        let stream = client.streamChat(fen: fen(), messages: wire, moveCount: moveCount(),
                                       gameId: gameId(), lastMove: lastMove(),
                                       coachVoice: coachVoice.wireValue, token: token)
        streamTask = Task { [weak self] in
            var buffer = ""
            for await event in stream {
                guard let self else { return }
                switch event {
                case let .chunk(fragment):
                    buffer += fragment
                    self.update(assistantId, text: buffer)
                case let .abort(reply, _, _):
                    buffer = reply
                    self.update(assistantId, text: reply)
                case .done, .error:
                    break
                }
            }
            guard let self else { return }
            if buffer.isEmpty { self.update(assistantId, text: Self.offlineFallback) }
            self.isStreaming = false
        }
    }

    /// Stop an in-flight stream (e.g. on teardown). The partial already shown is
    /// kept; the server persists whatever it validated.
    func cancelStreaming() {
        streamTask?.cancel()
        streamTask = nil
        isStreaming = false
    }

    /// Await the in-flight stream — test seam so a send can be observed
    /// deterministically. No-op when nothing is streaming.
    func awaitStreamCompletion() async {
        await streamTask?.value
    }

    private func update(_ id: UUID, text: String) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].text = text
    }

    /// Map a persisted turn to a rendered message. "system" rows (compaction
    /// summaries) and empty content are dropped.
    private static func message(from turn: ChatHistoryTurnDTO) -> Message? {
        let role: Message.Role
        switch turn.role {
        case "user": role = .user
        case "assistant": role = .assistant
        default: return nil
        }
        guard !turn.content.isEmpty else { return nil }
        return Message(id: UUID(), role: role, text: turn.content)
    }
}
