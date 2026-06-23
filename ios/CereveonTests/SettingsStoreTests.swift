import XCTest
@testable import Cereveon

/// A no-op `ChatClient` so a `ChatViewModel` can be built to verify it reads the
/// same persisted coach-voice key that `SettingsStore` writes.
private final class NoopChatClient: ChatClient {
    func chat(fen: String, messages: [ChatMessageDTO], moveCount: Int?,
              gameId: String?, lastMove: String?, coachVoice: String?, token: String) async -> APIResult<ChatResponse> {
        .httpError(501)
    }
    func history(limit: Int, gameId: String?, token: String) async -> APIResult<ChatHistoryResponse> {
        .httpError(501)
    }
    func streamChat(fen: String, messages: [ChatMessageDTO], moveCount: Int?,
                    gameId: String?, lastMove: String?, coachVoice: String?, token: String) -> AsyncStream<ChatStreamEvent> {
        AsyncStream { $0.finish() }
    }
}

@MainActor
final class SettingsStoreTests: XCTestCase {

    private func freshDefaults() -> UserDefaults {
        UserDefaults(suiteName: "SettingsStoreTests-\(UUID().uuidString)")!
    }

    func testFreshDefaults() {
        let store = SettingsStore(defaults: freshDefaults())
        XCTAssertEqual(store.coachVoice, .conversational)
        XCTAssertEqual(store.boardStyle, .flat)
        XCTAssertTrue(store.soundEnabled)
        XCTAssertTrue(store.notificationsEnabled)
    }

    func testPersistsAcrossInstances() {
        let defaults = freshDefaults()
        let a = SettingsStore(defaults: defaults)
        a.coachVoice = .terse
        a.boardStyle = .wireframe
        a.soundEnabled = false
        a.notificationsEnabled = false

        let b = SettingsStore(defaults: defaults)
        XCTAssertEqual(b.coachVoice, .terse)
        XCTAssertEqual(b.boardStyle, .wireframe)
        XCTAssertFalse(b.soundEnabled)
        XCTAssertFalse(b.notificationsEnabled)
    }

    func testStaticBoardStyleReader() {
        let defaults = freshDefaults()
        XCTAssertEqual(SettingsStore.boardStyle(defaults), .flat, "default")
        let store = SettingsStore(defaults: defaults)
        store.boardStyle = .engraved
        XCTAssertEqual(SettingsStore.boardStyle(defaults), .engraved)
    }

    /// The Settings voice radio and the chat-panel voice picker must edit the same
    /// persisted value (shared UserDefaults key).
    func testCoachVoiceSharesKeyWithChatViewModel() {
        let defaults = freshDefaults()
        let store = SettingsStore(defaults: defaults)
        store.coachVoice = .formal

        let chat = ChatViewModel(client: NoopChatClient(),
                                 fen: { "startpos" },
                                 token: { "t" },
                                 userDefaults: defaults)
        XCTAssertEqual(chat.coachVoice, .formal)
    }
}
