import XCTest
@testable import Cereveon

/// Exercises the coach-chat client (`/chat`, `/chat/history`, `/chat/stream`) and
/// the SSE event parser over the shared `URLProtocolStub` (defined in
/// AuthTests.swift) — no live server.
final class ChatApiClientTests: XCTestCase {

    override func setUp() {
        super.setUp()
        URLProtocolStub.handler = nil
        URLProtocolStub.lastRequest = nil
    }

    override func tearDown() {
        URLProtocolStub.handler = nil
        URLProtocolStub.lastRequest = nil
        super.tearDown()
    }

    private func stubConfig() -> URLSessionConfiguration {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [URLProtocolStub.self]
        return cfg
    }

    private func ok(_ json: String, headers: [String: String]? = nil) -> (URLRequest) -> (HTTPURLResponse, Data) {
        { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: headers)!,
             Data(json.utf8))
        }
    }

    // MARK: - POST /chat

    func testChatDecodesReplyAndSendsAuthAndRotates() async {
        var rotated: String?
        URLProtocolStub.handler = ok(
            #"{"reply":"Develop your knight.","engine_signal":{"evaluation":{"band":"slight_edge","side":"white"},"phase":"opening"}}"#,
            headers: ["X-Auth-Token": "rot2"]
        )
        let client = HTTPChatClient(baseURL: "https://test.local",
                                    configuration: stubConfig(),
                                    tokenSink: { rotated = $0 })
        let result = await client.chat(
            fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            messages: [ChatMessageDTO(role: "user", content: "what now?")],
            moveCount: 2, gameId: "g-1", lastMove: "e2e4", token: "tok"
        )
        guard case let .success(resp) = result else { return XCTFail("expected success: \(result)") }
        XCTAssertEqual(resp.reply, "Develop your knight.")
        XCTAssertEqual(resp.engineSignal?.evaluation?.band, "slight_edge")
        XCTAssertEqual(resp.engineSignal?.evaluation?.side, "white")
        XCTAssertEqual(resp.engineSignal?.phase, "opening")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "X-Api-Key"), AppConfig.apiKey)
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "X-API-Version"), AppConfig.apiVersion)
        XCTAssertEqual(rotated, "rot2")
    }

    func testChatLenientOnEmptyBody() async {
        URLProtocolStub.handler = ok("{}")
        let client = HTTPChatClient(baseURL: "https://test.local", configuration: stubConfig())
        let result = await client.chat(fen: "f", messages: [], moveCount: nil, gameId: nil, lastMove: nil, token: "t")
        guard case let .success(resp) = result else { return XCTFail("expected success: \(result)") }
        XCTAssertEqual(resp.reply, "")
        XCTAssertNil(resp.engineSignal)
    }

    func testChatHttpErrorPropagates() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 422, httpVersion: nil, headerFields: nil)!, Data())
        }
        let client = HTTPChatClient(baseURL: "https://test.local", configuration: stubConfig())
        let result = await client.chat(fen: "f", messages: [], moveCount: nil, gameId: nil, lastMove: nil, token: "t")
        guard case let .httpError(code) = result else { return XCTFail("expected httpError: \(result)") }
        XCTAssertEqual(code, 422)
    }

    // MARK: - GET /chat/history

    func testHistoryDecodesTurnsAndBuildsScopedQuery() async {
        URLProtocolStub.handler = ok(
            #"{"turns":[{"id":"1","role":"user","content":"hello","mode":"CHAT_V1"},{"id":"2","role":"assistant","content":"hi there","fen":"f","created_at":"2026-06-22T00:00:00Z"}]}"#
        )
        let client = HTTPChatClient(baseURL: "https://test.local", configuration: stubConfig())
        let result = await client.history(limit: 50, gameId: "g-1", token: "tok")
        guard case let .success(resp) = result else { return XCTFail("expected success: \(result)") }
        XCTAssertEqual(resp.turns.count, 2)
        XCTAssertEqual(resp.turns[0].role, "user")
        XCTAssertEqual(resp.turns[0].content, "hello")
        XCTAssertEqual(resp.turns[1].role, "assistant")
        XCTAssertEqual(resp.turns[1].content, "hi there")
        XCTAssertEqual(resp.turns[1].createdAt, "2026-06-22T00:00:00Z")
        let url = URLProtocolStub.lastRequest?.url?.absoluteString ?? ""
        XCTAssertTrue(url.contains("/chat/history?limit=50"), url)
        XCTAssertTrue(url.contains("game_id=g-1"), url)
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
    }

    func testHistoryEmptyDefaultsAndOmitsGameId() async {
        URLProtocolStub.handler = ok("{}")
        let client = HTTPChatClient(baseURL: "https://test.local", configuration: stubConfig())
        let result = await client.history(limit: 10, gameId: nil, token: "t")
        guard case let .success(resp) = result else { return XCTFail("expected success: \(result)") }
        XCTAssertTrue(resp.turns.isEmpty)
        XCTAssertFalse((URLProtocolStub.lastRequest?.url?.absoluteString ?? "").contains("game_id"))
    }

    // MARK: - SSE event parsing (pure)

    func testParseChunk() {
        XCTAssertEqual(ChatStreamEvent.parse(#"{"type":"chunk","text":"Hello"}"#), .chunk("Hello"))
    }

    func testParseDoneCarriesSignalAndMode() {
        let event = ChatStreamEvent.parse(
            #"{"type":"done","engine_signal":{"phase":"middlegame"},"mode":"CHAT_V1"}"#
        )
        guard case let .done(signal, mode) = event else {
            return XCTFail("expected done: \(String(describing: event))")
        }
        XCTAssertEqual(mode, "CHAT_V1")
        XCTAssertEqual(signal?.phase, "middlegame")
    }

    func testParseAbortCarriesFallbackReply() {
        let event = ChatStreamEvent.parse(#"{"type":"abort","reply":"Let's keep it simple.","mode":"CHAT_V1"}"#)
        guard case let .abort(reply, _, mode) = event else {
            return XCTFail("expected abort: \(String(describing: event))")
        }
        XCTAssertEqual(reply, "Let's keep it simple.")
        XCTAssertEqual(mode, "CHAT_V1")
    }

    func testParseErrorUnknownAndMalformed() {
        XCTAssertEqual(ChatStreamEvent.parse(#"{"type":"error","message":"boom"}"#), .error("boom"))
        XCTAssertNil(ChatStreamEvent.parse(#"{"type":"keepalive"}"#))
        XCTAssertNil(ChatStreamEvent.parse("not json"))
        XCTAssertNil(ChatStreamEvent.parse(#"{"no_type":true}"#))
    }

    // MARK: - SSE transport over the stub

    func testStreamChatYieldsChunksThenDone() async {
        let body = [
            #"data: {"type":"chunk","text":"Open "}"#,
            "",
            #"data: {"type":"chunk","text":"with e4."}"#,
            "",
            #"data: {"type":"done","mode":"CHAT_V1"}"#,
            "",
        ].joined(separator: "\n")
        URLProtocolStub.handler = ok(body)
        let client = HTTPChatClient(baseURL: "https://test.local", configuration: stubConfig())

        var events: [ChatStreamEvent] = []
        for await event in client.streamChat(fen: "f", messages: [], moveCount: nil,
                                              gameId: nil, lastMove: nil, token: "t") {
            events.append(event)
        }
        XCTAssertEqual(events.count, 3, "\(events)")
        XCTAssertEqual(events[0], .chunk("Open "))
        XCTAssertEqual(events[1], .chunk("with e4."))
        guard case .done = events[2] else { return XCTFail("expected done last: \(events)") }
    }

    func testStreamChatHttpErrorYieldsErrorEvent() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!, Data())
        }
        let client = HTTPChatClient(baseURL: "https://test.local", configuration: stubConfig())

        var events: [ChatStreamEvent] = []
        for await event in client.streamChat(fen: "f", messages: [], moveCount: nil,
                                              gameId: nil, lastMove: nil, token: "t") {
            events.append(event)
        }
        XCTAssertEqual(events, [.error("HTTP 500")])
    }
}
