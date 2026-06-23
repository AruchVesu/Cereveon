import XCTest
@testable import Cereveon

/// A scripted `ChatClient` for the view-model tests: returns a canned history and
/// replays a fixed list of stream events, recording what the last `streamChat`
/// was asked to send.
private final class FakeChatClient: ChatClient {
    var historyResult: APIResult<ChatHistoryResponse> = .success(decodeHistory("{}"))
    var streamEvents: [ChatStreamEvent] = []
    private(set) var historyCallCount = 0
    private(set) var lastStreamMessages: [ChatMessageDTO] = []
    private(set) var lastFen: String?
    private(set) var lastGameId: String?
    private(set) var lastMove: String?
    private(set) var lastCoachVoice: String?
    private(set) var feedbackCallCount = 0
    private(set) var lastFeedbackFen: String?
    private(set) var lastFeedbackHelpful: Bool?

    func chat(fen: String, messages: [ChatMessageDTO], moveCount: Int?,
              gameId: String?, lastMove: String?, coachVoice: String?, token: String) async -> APIResult<ChatResponse> {
        .httpError(501)
    }

    func submitFeedback(sessionFen: String, isHelpful: Bool, token: String) async -> APIResult<Void> {
        feedbackCallCount += 1
        lastFeedbackFen = sessionFen
        lastFeedbackHelpful = isHelpful
        return .success(())
    }

    func history(limit: Int, gameId: String?, token: String) async -> APIResult<ChatHistoryResponse> {
        historyCallCount += 1
        return historyResult
    }

    func streamChat(fen: String, messages: [ChatMessageDTO], moveCount: Int?,
                    gameId: String?, lastMove: String?, coachVoice: String?, token: String) -> AsyncStream<ChatStreamEvent> {
        lastFen = fen
        lastStreamMessages = messages
        lastGameId = gameId
        self.lastMove = lastMove
        lastCoachVoice = coachVoice
        let events = streamEvents
        return AsyncStream { continuation in
            for event in events { continuation.yield(event) }
            continuation.finish()
        }
    }
}

private func decodeHistory(_ json: String) -> ChatHistoryResponse {
    try! APIJSON.decode(ChatHistoryResponse.self, from: Data(json.utf8))
}

/// A throwaway, isolated `UserDefaults` domain so voice-persistence tests never
/// pollute (or read) the shared `.standard` suite.
private func freshDefaults() -> UserDefaults {
    UserDefaults(suiteName: "ChatViewModelTests-\(UUID().uuidString)")!
}

@MainActor
final class ChatViewModelTests: XCTestCase {

    private func makeVM(_ client: FakeChatClient,
                        fen: String = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                        gameId: String? = "g-1",
                        lastMove: String? = "e2e4",
                        token: String? = "tok",
                        userDefaults: UserDefaults = freshDefaults()) -> ChatViewModel {
        ChatViewModel(client: client,
                      fen: { fen },
                      gameId: { gameId },
                      lastMove: { lastMove },
                      moveCount: { 2 },
                      token: { token },
                      userDefaults: userDefaults)
    }

    // MARK: - History seeding

    func testPreloadSeedsMessagesFromHistory() async {
        let client = FakeChatClient()
        client.historyResult = .success(decodeHistory(
            #"{"turns":[{"id":"1","role":"user","content":"plan?"},{"id":"2","role":"assistant","content":"Develop."},{"id":"3","role":"system","content":"summary"}]}"#
        ))
        let vm = makeVM(client)

        await vm.preloadHistory()

        XCTAssertTrue(vm.historyLoaded)
        // The "system" compaction row is dropped; only user + assistant render.
        XCTAssertEqual(vm.messages.count, 2)
        XCTAssertEqual(vm.messages[0].role, .user)
        XCTAssertEqual(vm.messages[0].text, "plan?")
        XCTAssertEqual(vm.messages[1].role, .assistant)
        XCTAssertEqual(vm.messages[1].text, "Develop.")
    }

    func testPreloadIsOnlyFetchedOnce() async {
        let client = FakeChatClient()
        await makeVMAndPreloadTwice(client)
        XCTAssertEqual(client.historyCallCount, 1)
    }

    private func makeVMAndPreloadTwice(_ client: FakeChatClient) async {
        let vm = makeVM(client)
        await vm.preloadHistory()
        await vm.preloadHistory()
    }

    func testPreloadSkippedWhenLoggedOut() async {
        let client = FakeChatClient()
        let vm = makeVM(client, token: nil)
        await vm.preloadHistory()
        XCTAssertEqual(client.historyCallCount, 0)
        XCTAssertFalse(vm.historyLoaded)
    }

    // MARK: - Sending + streaming

    func testSendStreamsAssistantReplyAndSendsLivePosition() async {
        let client = FakeChatClient()
        client.streamEvents = [.chunk("Develop "), .chunk("your knight."), .done(engineSignal: nil, mode: "CHAT_V1")]
        let vm = makeVM(client)
        vm.draft = "what now?"

        vm.send()
        await vm.awaitStreamCompletion()

        XCTAssertEqual(vm.messages.count, 2)
        XCTAssertEqual(vm.messages[0].role, .user)
        XCTAssertEqual(vm.messages[0].text, "what now?")
        XCTAssertEqual(vm.messages[1].role, .assistant)
        XCTAssertEqual(vm.messages[1].text, "Develop your knight.")
        XCTAssertFalse(vm.isStreaming)
        XCTAssertEqual(vm.draft, "")
        // The turn carried the live board, game scope, and last move.
        XCTAssertEqual(client.lastFen, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        XCTAssertEqual(client.lastGameId, "g-1")
        XCTAssertEqual(client.lastMove, "e2e4")
        // The user turn is in the replayed history (the empty assistant
        // placeholder is NOT).
        XCTAssertEqual(client.lastStreamMessages.count, 1)
        XCTAssertEqual(client.lastStreamMessages[0].role, "user")
        XCTAssertEqual(client.lastStreamMessages[0].content, "what now?")
    }

    func testAbortReplacesPartialWithFallbackReply() async {
        let client = FakeChatClient()
        client.streamEvents = [.chunk("Maybe "), .abort(reply: "Let's keep it simple.", engineSignal: nil, mode: "CHAT_V1")]
        let vm = makeVM(client)
        vm.draft = "hi"

        vm.send()
        await vm.awaitStreamCompletion()

        XCTAssertEqual(vm.messages[1].text, "Let's keep it simple.")
    }

    func testEmptyStreamShowsOfflineFallback() async {
        let client = FakeChatClient()
        client.streamEvents = [.error("HTTP 500")] // no chunks
        let vm = makeVM(client)
        vm.draft = "hi"

        vm.send()
        await vm.awaitStreamCompletion()

        XCTAssertEqual(vm.messages[1].text, ChatViewModel.offlineFallback)
        XCTAssertFalse(vm.isStreaming)
    }

    func testNoEventsAtAllShowsOfflineFallback() async {
        let client = FakeChatClient()
        client.streamEvents = []
        let vm = makeVM(client)
        vm.draft = "hi"

        vm.send()
        await vm.awaitStreamCompletion()

        XCTAssertEqual(vm.messages[1].text, ChatViewModel.offlineFallback)
    }

    // MARK: - canSend gating

    func testCanSendGating() {
        let client = FakeChatClient()
        let vm = makeVM(client)
        XCTAssertFalse(vm.canSend, "empty draft → cannot send")
        vm.draft = "   "
        XCTAssertFalse(vm.canSend, "whitespace-only draft → cannot send")
        vm.draft = "hello"
        XCTAssertTrue(vm.canSend)
    }

    func testCannotSendWhenLoggedOut() {
        let client = FakeChatClient()
        let vm = makeVM(client, token: nil)
        vm.draft = "hello"
        XCTAssertFalse(vm.canSend)
    }

    // MARK: - Coach voice

    func testSelectedVoiceIsSentWithEachTurn() async {
        let client = FakeChatClient()
        client.streamEvents = [.chunk("ok."), .done(engineSignal: nil, mode: "CHAT_V1")]
        let vm = makeVM(client)
        vm.coachVoice = .terse
        vm.draft = "hi"

        vm.send()
        await vm.awaitStreamCompletion()

        XCTAssertEqual(client.lastCoachVoice, "terse")
    }

    func testVoiceDefaultsToConversationalAndPersists() {
        let defaults = freshDefaults()
        let first = makeVM(FakeChatClient(), userDefaults: defaults)
        XCTAssertEqual(first.coachVoice, .conversational, "fresh install → conversational")

        first.coachVoice = .formal
        // A new instance over the same store restores the choice.
        let second = makeVM(FakeChatClient(), userDefaults: defaults)
        XCTAssertEqual(second.coachVoice, .formal)
        XCTAssertEqual(second.coachVoice.wireValue, "formal")
    }

    // MARK: - Thumbs feedback

    func testSubmitFeedbackSendsReplyPositionAndHelpful() async {
        let client = FakeChatClient()
        client.streamEvents = [.chunk("Develop."), .done(engineSignal: nil, mode: "CHAT_V1")]
        let vm = makeVM(client, fen: "8/8/8/8/8/8/8/8 w - - 0 1")
        vm.draft = "hi"
        vm.send()
        await vm.awaitStreamCompletion()

        let reply = vm.messages.last!
        XCTAssertEqual(reply.role, .assistant)
        XCTAssertEqual(reply.fen, "8/8/8/8/8/8/8/8 w - - 0 1", "the reply carries the position it was about")

        vm.submitFeedback(for: reply, helpful: true)
        await vm.awaitFeedbackCompletion()

        XCTAssertEqual(client.feedbackCallCount, 1)
        XCTAssertEqual(client.lastFeedbackHelpful, true)
        XCTAssertEqual(client.lastFeedbackFen, "8/8/8/8/8/8/8/8 w - - 0 1")
        // The bubble is optimistically marked.
        XCTAssertEqual(vm.messages.last?.feedback, true)
    }

    func testFeedbackIgnoredForUserMessageAndWhenLoggedOut() async {
        let client = FakeChatClient()
        let vm = makeVM(client)
        // A synthetic user message is never rated.
        let userMessage = ChatViewModel.Message(id: UUID(), role: .user, text: "hi")
        vm.submitFeedback(for: userMessage, helpful: true)
        await vm.awaitFeedbackCompletion()
        XCTAssertEqual(client.feedbackCallCount, 0)

        // Logged out → no call even for an assistant message.
        let loggedOut = makeVM(client, token: nil)
        let assistant = ChatViewModel.Message(id: UUID(), role: .assistant, text: "ok", fen: "startpos")
        loggedOut.submitFeedback(for: assistant, helpful: false)
        await loggedOut.awaitFeedbackCompletion()
        XCTAssertEqual(client.feedbackCallCount, 0)
    }
}
