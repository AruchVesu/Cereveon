import Foundation

/// Client for the backend authentication endpoints. Every call returns an
/// `APIResult` — callers never see raw errors. Mirrors the Android `AuthApiClient`.
protocol AuthApiClient {
    func login(email: String, password: String) async -> APIResult<LoginResponse>
    func register(email: String, password: String) async -> APIResult<LoginResponse>
    func logout(token: String) async -> APIResult<Void>
    func me(token: String) async -> APIResult<MeResponse>
    func updateMe(token: String, rating: Double?, confidence: Double?) async -> APIResult<MeResponse>
    func changePassword(currentPassword: String, newPassword: String, token: String) async -> APIResult<Void>
}

/// Production `AuthApiClient` over `BaseHTTPClient`, mirroring the Android
/// `HttpAuthApiClient`: the `POST + X-HTTP-Method-Override: PATCH` path for
/// `/auth/me`, and consuming the `X-Auth-Token` rotation header on every
/// authenticated call (`me` / `updateMe` / `changePassword`).
final class HTTPAuthApiClient: AuthApiClient {
    private let http: BaseHTTPClient
    private let tokenSink: ((String) -> Void)?

    init(baseURL: String = AppConfig.apiBase,
         delegate: URLSessionDelegate? = nil,
         configuration: URLSessionConfiguration? = nil,
         tokenSink: ((String) -> Void)? = nil) {
        self.http = BaseHTTPClient(baseURL: baseURL, delegate: delegate, configuration: configuration)
        self.tokenSink = tokenSink
    }

    private func bearer(_ token: String) -> [String: String] {
        ["Authorization": "Bearer \(token)"]
    }

    /// Mirrors Android's `consumeRefreshedToken`: hand any non-blank
    /// `X-Auth-Token` to the sink. Header lookup is case-insensitive.
    private func consumeRefresh(_ response: HTTPURLResponse) {
        guard let sink = tokenSink,
              let refreshed = response.value(forHTTPHeaderField: AppConfig.authTokenRefreshHeader),
              !refreshed.trimmingCharacters(in: .whitespaces).isEmpty
        else { return }
        sink(refreshed)
    }

    func login(email: String, password: String) async -> APIResult<LoginResponse> {
        let body = try? APIJSON.encode(LoginRequest(email: email, password: password))
        return await http.request(
            path: "/auth/login", method: "POST", body: body,
            decode: { try APIJSON.decode(LoginResponse.self, from: $0) }
        )
    }

    func register(email: String, password: String) async -> APIResult<LoginResponse> {
        let body = try? APIJSON.encode(RegisterRequest(email: email, password: password))
        return await http.request(
            path: "/auth/register", method: "POST", body: body,
            successCodes: [200, 201],   // register also returns 201 Created
            decode: { try APIJSON.decode(LoginResponse.self, from: $0) }
        )
    }

    func logout(token: String) async -> APIResult<Void> {
        await http.requestVoid(path: "/auth/logout", method: "POST", headers: bearer(token))
    }

    func me(token: String) async -> APIResult<MeResponse> {
        await http.request(
            path: "/auth/me", method: "GET", headers: bearer(token),
            onResponse: consumeRefresh,
            decode: { try APIJSON.decode(MeResponse.self, from: $0) }
        )
    }

    func updateMe(token: String, rating: Double?, confidence: Double?) async -> APIResult<MeResponse> {
        let body = try? APIJSON.encode(UpdateMeRequest(rating: rating, confidence: confidence))
        var headers = bearer(token)
        headers["X-HTTP-Method-Override"] = "PATCH"
        return await http.request(
            path: "/auth/me", method: "POST", headers: headers, body: body,
            onResponse: consumeRefresh,
            decode: { try APIJSON.decode(MeResponse.self, from: $0) }
        )
    }

    func changePassword(currentPassword: String, newPassword: String, token: String) async -> APIResult<Void> {
        let body = try? APIJSON.encode(
            ChangePasswordRequest(currentPassword: currentPassword, newPassword: newPassword)
        )
        return await http.requestVoid(
            path: "/auth/change-password", method: "POST", headers: bearer(token), body: body,
            onResponse: consumeRefresh
        )
    }
}
