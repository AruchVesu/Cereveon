import Foundation

private func repertoireHeaders(token: String) -> [String: String] {
    [AppConfig.apiKeyHeader: AppConfig.apiKey, "Authorization": "Bearer \(token)"]
}

private func consumeRepertoireRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// Opening-repertoire endpoints. Every editing call returns the full updated
/// repertoire so the caller re-renders from the authoritative response.
protocol RepertoireClient {
    func getRepertoire(token: String) async -> APIResult<RepertoireResponse>
    func addOpening(eco: String, name: String, line: String, token: String) async -> APIResult<RepertoireResponse>
    func deleteOpening(eco: String, token: String) async -> APIResult<RepertoireResponse>
    func setActive(eco: String, token: String) async -> APIResult<RepertoireResponse>
    func drillResult(eco: String, outcome: Double, token: String) async -> APIResult<RepertoireResponse>
}

final class HTTPRepertoireClient: RepertoireClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    private func decodeRepertoire(_ data: Data) throws -> RepertoireResponse {
        try APIJSON.decode(RepertoireResponse.self, from: data)
    }

    private func eco(_ eco: String) -> String {
        eco.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? eco
    }

    func getRepertoire(token: String) async -> APIResult<RepertoireResponse> {
        await http.request(
            path: "/repertoire", method: "GET",
            headers: repertoireHeaders(token: token),
            onResponse: { [tokenSink] in consumeRepertoireRotation($0, tokenSink) },
            decode: decodeRepertoire
        )
    }

    func addOpening(eco: String, name: String, line: String, token: String) async -> APIResult<RepertoireResponse> {
        let body = try? APIJSON.encode(RepertoireAddRequest(eco: eco, name: name, line: line))
        return await http.request(
            path: "/repertoire", method: "POST",
            headers: repertoireHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeRepertoireRotation($0, tokenSink) },
            decode: decodeRepertoire
        )
    }

    func deleteOpening(eco: String, token: String) async -> APIResult<RepertoireResponse> {
        await http.request(
            path: "/repertoire/\(self.eco(eco))", method: "DELETE",
            headers: repertoireHeaders(token: token),
            onResponse: { [tokenSink] in consumeRepertoireRotation($0, tokenSink) },
            decode: decodeRepertoire
        )
    }

    func setActive(eco: String, token: String) async -> APIResult<RepertoireResponse> {
        await http.request(
            path: "/repertoire/\(self.eco(eco))/active", method: "POST",
            headers: repertoireHeaders(token: token),
            onResponse: { [tokenSink] in consumeRepertoireRotation($0, tokenSink) },
            decode: decodeRepertoire
        )
    }

    func drillResult(eco: String, outcome: Double, token: String) async -> APIResult<RepertoireResponse> {
        let body = try? APIJSON.encode(DrillResultRequest(outcome: outcome))
        return await http.request(
            path: "/repertoire/\(self.eco(eco))/drill-result", method: "POST",
            headers: repertoireHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeRepertoireRotation($0, tokenSink) },
            decode: decodeRepertoire
        )
    }
}
