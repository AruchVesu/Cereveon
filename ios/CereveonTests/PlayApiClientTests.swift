import XCTest
@testable import Cereveon

/// Exercises the play-loop API clients over the shared `URLProtocolStub`
/// (defined in AuthTests.swift) — no live server.
final class PlayApiClientTests: XCTestCase {

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

    // MARK: /engine/eval

    func testEvalDecodesScoreAndSendsApiKey() async {
        URLProtocolStub.handler = ok(#"{"score":120,"best_move":"e2e4","source":"engine"}"#)
        let client = HTTPEngineEvalClient(baseURL: "https://test.local", configuration: stubConfig())
        let result = await client.evaluate(fen: "8/8/8/8/8/8/8/8 w - - 0 1")
        guard case let .success(eval) = result else { return XCTFail("expected success: \(result)") }
        XCTAssertEqual(eval.score, 120)
        XCTAssertEqual(eval.bestMove, "e2e4")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "X-Api-Key"), AppConfig.apiKey)
    }

    func testEvalEmptyBestMoveBecomesNil() async {
        URLProtocolStub.handler = ok(#"{"score":null,"best_move":"","source":"unavailable"}"#)
        let client = HTTPEngineEvalClient(baseURL: "https://test.local", configuration: stubConfig())
        guard case let .success(eval) = await client.evaluate(fen: "x") else { return XCTFail() }
        XCTAssertNil(eval.score)
        XCTAssertNil(eval.bestMove)
    }

    // MARK: /live/move

    func testLiveMoveSendsAuthAndConsumesRotation() async {
        var rotated: String?
        URLProtocolStub.handler = ok(
            #"{"status":"ok","hint":"Develop your pieces.","move_quality":"good","mode":"LIVE_V1"}"#,
            headers: ["X-Auth-Token": "rot"]
        )
        let client = HTTPLiveMoveClient(baseURL: "https://test.local",
                                        configuration: stubConfig(),
                                        tokenSink: { rotated = $0 })
        let result = await client.liveCoaching(fen: "f", uci: "e2e4", fenBefore: "b", token: "tok")
        guard case let .success(resp) = result else { return XCTFail("expected success: \(result)") }
        XCTAssertEqual(resp.hint, "Develop your pieces.")
        XCTAssertEqual(resp.moveQuality, "good")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "X-Api-Key"), AppConfig.apiKey)
        XCTAssertEqual(rotated, "rot")
    }

    // MARK: /game

    func testGameStartDecodesGameId() async {
        URLProtocolStub.handler = ok(#"{"game_id":"g-123"}"#)
        let client = HTTPGameClient(baseURL: "https://test.local", configuration: stubConfig())
        guard case let .success(resp) = await client.startGame(token: "tok") else { return XCTFail() }
        XCTAssertEqual(resp.gameId, "g-123")
    }

    func testGameFinishDecodesNewRating() async {
        URLProtocolStub.handler = ok(#"{"status":"stored","new_rating":1234.5}"#)
        let client = HTTPGameClient(baseURL: "https://test.local", configuration: stubConfig())
        let request = GameFinishRequest(pgn: "[Event \"x\"]\n\n1. e4 e5", result: "win", accuracy: 0.5)
        guard case let .success(resp) = await client.finishGame(request, token: "tok") else { return XCTFail() }
        XCTAssertEqual(resp.newRating, 1234.5)
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
    }
}
