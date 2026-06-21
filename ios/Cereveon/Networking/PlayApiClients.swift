import Foundation

/// Hand any non-blank `X-Auth-Token` rotation header to the sink (mirrors the
/// Android `consumeRefreshedToken`). Used by the authenticated play-loop clients.
private func consumeRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

private func playHeaders(token: String?) -> [String: String] {
    var headers = [AppConfig.apiKeyHeader: AppConfig.apiKey]
    if let token { headers["Authorization"] = "Bearer \(token)" }
    return headers
}

// MARK: - /engine/eval

protocol EngineEvalClient {
    func evaluate(fen: String) async -> APIResult<EngineEvalResponse>
}

/// POST /engine/eval — X-Api-Key only (the rate-limit-shield key; no Bearer).
final class HTTPEngineEvalClient: EngineEvalClient {
    private let http: BaseHTTPClient

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
    }

    func evaluate(fen: String) async -> APIResult<EngineEvalResponse> {
        let body = try? APIJSON.encode(EngineEvalRequest(fen: fen))
        return await http.request(
            path: "/engine/eval", method: "POST",
            headers: playHeaders(token: nil), body: body,
            decode: { try APIJSON.decode(EngineEvalResponse.self, from: $0) }
        )
    }
}

// MARK: - /live/move

protocol LiveMoveClient {
    func liveCoaching(fen: String, uci: String, fenBefore: String?, token: String) async -> APIResult<LiveMoveResponse>
}

/// POST /live/move — X-Api-Key + Bearer (the route is `Depends(get_current_player)`,
/// so a missing Bearer is a certain 401). Consumes the `X-Auth-Token` rotation.
final class HTTPLiveMoveClient: LiveMoveClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func liveCoaching(fen: String, uci: String, fenBefore: String?, token: String) async -> APIResult<LiveMoveResponse> {
        let body = try? APIJSON.encode(LiveMoveRequest(fen: fen, uci: uci, fenBefore: fenBefore))
        return await http.request(
            path: "/live/move", method: "POST",
            headers: playHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeRotation($0, tokenSink) },
            decode: { try APIJSON.decode(LiveMoveResponse.self, from: $0) }
        )
    }
}

// MARK: - /game/start, /game/finish

protocol GameClient {
    func startGame(token: String) async -> APIResult<GameStartResponse>
    func finishGame(_ request: GameFinishRequest, token: String) async -> APIResult<GameFinishResponse>
}

/// POST /game/start + /game/finish — X-Api-Key + Bearer; both rotate the token.
final class HTTPGameClient: GameClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func startGame(token: String) async -> APIResult<GameStartResponse> {
        let body = try? APIJSON.encode(GameStartRequest(playerId: "ios"))
        return await http.request(
            path: "/game/start", method: "POST",
            headers: playHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeRotation($0, tokenSink) },
            decode: { try APIJSON.decode(GameStartResponse.self, from: $0) }
        )
    }

    func finishGame(_ request: GameFinishRequest, token: String) async -> APIResult<GameFinishResponse> {
        let body = try? APIJSON.encode(request)
        return await http.request(
            path: "/game/finish", method: "POST",
            headers: playHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeRotation($0, tokenSink) },
            decode: { try APIJSON.decode(GameFinishResponse.self, from: $0) }
        )
    }
}
