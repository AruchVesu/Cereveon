import Foundation

private func verifyHeaders(token: String) -> [String: String] {
    [AppConfig.apiKeyHeader: AppConfig.apiKey, "Authorization": "Bearer \(token)"]
}

private func consumeVerifyRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// POST /training/verify-replay — judges one move against the engine.
protocol VerifyReplayClient {
    func verify(fen: String, moveUci: String, token: String) async -> APIResult<VerifyReplayResponse>
}

final class HTTPVerifyReplayClient: VerifyReplayClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        // The engine has to run, so allow the longer chat-length deadline.
        http = BaseHTTPClient(baseURL: baseURL, readTimeout: AppConfig.chatReadTimeout,
                              delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func verify(fen: String, moveUci: String, token: String) async -> APIResult<VerifyReplayResponse> {
        let body = try? APIJSON.encode(VerifyReplayRequest(fen: fen, moveUci: moveUci))
        return await http.request(
            path: "/training/verify-replay", method: "POST",
            headers: verifyHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeVerifyRotation($0, tokenSink) },
            decode: { try APIJSON.decode(VerifyReplayResponse.self, from: $0) }
        )
    }
}
