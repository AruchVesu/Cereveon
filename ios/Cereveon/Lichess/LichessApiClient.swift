import Foundation

private func lichessHeaders(token: String) -> [String: String] {
    [AppConfig.apiKeyHeader: AppConfig.apiKey, "Authorization": "Bearer \(token)"]
}

private func consumeLichessRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// Lichess integration endpoints. Linking is by username; import is the v2 async
/// path (POST returns 202 + a job; the caller polls `importJob`).
protocol LichessClient {
    func status(token: String) async -> APIResult<LichessStatus>
    func link(username: String, token: String) async -> APIResult<Void>
    func unlink(token: String) async -> APIResult<Void>
    func importGames(maxGames: Int, token: String) async -> APIResult<LichessImportJob>
    func importJob(jobId: String, token: String) async -> APIResult<LichessImportJob>
}

final class HTTPLichessClient: LichessClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func status(token: String) async -> APIResult<LichessStatus> {
        await http.request(
            path: "/lichess/status", method: "GET",
            headers: lichessHeaders(token: token),
            onResponse: { [tokenSink] in consumeLichessRotation($0, tokenSink) },
            decode: { try APIJSON.decode(LichessStatus.self, from: $0) }
        )
    }

    func link(username: String, token: String) async -> APIResult<Void> {
        let body = try? APIJSON.encode(LichessLinkRequest(username: username))
        return await http.requestVoid(
            path: "/lichess/link", method: "POST",
            headers: lichessHeaders(token: token), body: body,
            onResponse: { [tokenSink] in consumeLichessRotation($0, tokenSink) }
        )
    }

    func unlink(token: String) async -> APIResult<Void> {
        await http.requestVoid(
            path: "/lichess/link", method: "DELETE",
            headers: lichessHeaders(token: token),
            onResponse: { [tokenSink] in consumeLichessRotation($0, tokenSink) }
        )
    }

    func importGames(maxGames: Int, token: String) async -> APIResult<LichessImportJob> {
        // X-API-Version: 2 (set by BaseHTTPClient) selects the async path → 202 + a job.
        await http.request(
            path: "/lichess/import?max_games=\(maxGames)&rated=true", method: "POST",
            headers: lichessHeaders(token: token),
            successCodes: [200, 202],
            onResponse: { [tokenSink] in consumeLichessRotation($0, tokenSink) },
            decode: { try APIJSON.decode(LichessImportJob.self, from: $0) }
        )
    }

    func importJob(jobId: String, token: String) async -> APIResult<LichessImportJob> {
        let encoded = jobId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? jobId
        return await http.request(
            path: "/lichess/import/job/\(encoded)", method: "GET",
            headers: lichessHeaders(token: token),
            onResponse: { [tokenSink] in consumeLichessRotation($0, tokenSink) },
            decode: { try APIJSON.decode(LichessImportJob.self, from: $0) }
        )
    }
}
