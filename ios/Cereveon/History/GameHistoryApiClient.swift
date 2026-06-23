import Foundation

private func historyHeaders(token: String) -> [String: String] {
    [AppConfig.apiKeyHeader: AppConfig.apiKey, "Authorization": "Bearer \(token)"]
}

private func consumeHistoryRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// Past-games endpoints: the history list and a game's replay positions.
protocol GameHistoryClient {
    func history(token: String) async -> APIResult<GameHistoryResponse>
    func positions(eventId: String, token: String) async -> APIResult<GamePositionsResponse>
}

final class HTTPGameHistoryClient: GameHistoryClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func history(token: String) async -> APIResult<GameHistoryResponse> {
        await http.request(
            path: "/game/history", method: "GET",
            headers: historyHeaders(token: token),
            onResponse: { [tokenSink] in consumeHistoryRotation($0, tokenSink) },
            decode: { try APIJSON.decode(GameHistoryResponse.self, from: $0) }
        )
    }

    func positions(eventId: String, token: String) async -> APIResult<GamePositionsResponse> {
        // eventId is a server-issued id; percent-encode defensively for the path.
        let encoded = eventId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? eventId
        return await http.request(
            path: "/game/\(encoded)/positions", method: "GET",
            headers: historyHeaders(token: token),
            onResponse: { [tokenSink] in consumeHistoryRotation($0, tokenSink) },
            decode: { try APIJSON.decode(GamePositionsResponse.self, from: $0) }
        )
    }
}
