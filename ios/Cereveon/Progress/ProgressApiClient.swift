import Foundation

private func progressHeaders(token: String) -> [String: String] {
    [AppConfig.apiKeyHeader: AppConfig.apiKey, "Authorization": "Bearer \(token)"]
}

private func consumeProgressRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// GET /player/progress — the progress dashboard snapshot.
protocol ProgressClient {
    func progress(token: String) async -> APIResult<PlayerProgressResponse>
}

final class HTTPProgressClient: ProgressClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?
    /// PLAIN decoder so dictionary keys (`tactical_vision`, …) stay literal — see
    /// ProgressApiModels.
    private let decoder = JSONDecoder()

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func progress(token: String) async -> APIResult<PlayerProgressResponse> {
        await http.request(
            path: "/player/progress", method: "GET",
            headers: progressHeaders(token: token),
            onResponse: { [tokenSink] in consumeProgressRotation($0, tokenSink) },
            decode: { [decoder] in try decoder.decode(PlayerProgressResponse.self, from: $0) }
        )
    }
}
