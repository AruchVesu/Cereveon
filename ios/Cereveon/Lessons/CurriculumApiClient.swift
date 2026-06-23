import Foundation

private func curriculumHeaders(token: String) -> [String: String] {
    [AppConfig.apiKeyHeader: AppConfig.apiKey, "Authorization": "Bearer \(token)"]
}

private func consumeCurriculumRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// POST /curriculum/next — the next recommended study focus.
protocol CurriculumClient {
    func next(token: String) async -> APIResult<CurriculumNext>
}

final class HTTPCurriculumClient: CurriculumClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func next(token: String) async -> APIResult<CurriculumNext> {
        await http.request(
            path: "/curriculum/next", method: "POST",
            headers: curriculumHeaders(token: token),
            onResponse: { [tokenSink] in consumeCurriculumRotation($0, tokenSink) },
            decode: { try APIJSON.decode(CurriculumNext.self, from: $0) }
        )
    }
}
