import Foundation

private func studyPlanHeaders(token: String) -> [String: String] {
    [AppConfig.apiKeyHeader: AppConfig.apiKey, "Authorization": "Bearer \(token)"]
}

private func consumeStudyPlanRotation(_ response: HTTPURLResponse, _ sink: ((String) -> Void)?) {
    guard let sink,
          let token = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
          !token.trimmingCharacters(in: .whitespaces).isEmpty
    else { return }
    sink(token)
}

/// GET /coach/plan/today — the active study plan + today's due puzzle, or nil.
protocol StudyPlanClient {
    func today(token: String) async -> APIResult<TodayPlan?>
}

final class HTTPStudyPlanClient: StudyPlanClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    func today(token: String) async -> APIResult<TodayPlan?> {
        await http.request(
            path: "/coach/plan/today", method: "GET",
            headers: studyPlanHeaders(token: token),
            onResponse: { [tokenSink] in consumeStudyPlanRotation($0, tokenSink) },
            decode: { data in
                // The endpoint returns a bare `null` (200) when there's no active
                // plan; treat that — and an empty body — as "no card".
                let trimmed = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed == "null" || (trimmed?.isEmpty ?? true) { return nil }
                return try APIJSON.decode(TodayPlan.self, from: data)
            }
        )
    }
}
