import XCTest
@testable import Cereveon

// MARK: - Test doubles

/// Intercepts URLSession traffic so the API client can be exercised without a
/// live server. Returns a canned response and records the last request's headers
/// (the request body is intentionally not asserted — URLProtocol strips it to a
/// stream; encode behaviour is covered directly by the DTO tests).
final class URLProtocolStub: URLProtocol {
    static var handler: ((URLRequest) -> (HTTPURLResponse, Data))?
    static var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        URLProtocolStub.lastRequest = request
        guard let handler = URLProtocolStub.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        let (response, data) = handler(request)
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

final class FakeAuthApi: AuthApiClient {
    var loginResult: APIResult<LoginResponse> = .httpError(500)
    var updateResult: APIResult<MeResponse> = .httpError(500)
    var logoutCalled = false

    func login(email: String, password: String) async -> APIResult<LoginResponse> { loginResult }
    func register(email: String, password: String) async -> APIResult<LoginResponse> { loginResult }
    func logout(token: String) async -> APIResult<Void> { logoutCalled = true; return .success(()) }
    func me(token: String) async -> APIResult<MeResponse> { updateResult }
    func updateMe(token: String, rating: Double?, confidence: Double?) async -> APIResult<MeResponse> { updateResult }
    func changePassword(currentPassword: String, newPassword: String, token: String) async -> APIResult<Void> { .success(()) }
}

// MARK: - Tests

final class AuthTests: XCTestCase {

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

    // MARK: Helpers

    static func makeJWT(exp: Int, playerId: String = "pid") -> String {
        let header = Data(#"{"alg":"HS256","typ":"JWT"}"#.utf8)
        let payload = try! JSONSerialization.data(withJSONObject: ["exp": exp, "player_id": playerId])
        func b64url(_ d: Data) -> String {
            d.base64EncodedString()
                .replacingOccurrences(of: "+", with: "-")
                .replacingOccurrences(of: "/", with: "_")
                .replacingOccurrences(of: "=", with: "")
        }
        return "\(b64url(header)).\(b64url(payload)).sig"
    }

    static func loginResponseJSON(token: String, playerId: String = "pid") -> Data {
        Data(#"{"access_token":"\#(token)","player_id":"\#(playerId)","token_type":"bearer"}"#.utf8)
    }

    static func meResponseJSON() -> Data {
        Data(#"{"id":"x","email":"e@x.com","rating":1500,"confidence":0.5,"skill_vector":{},"training_xp":3}"#.utf8)
    }

    private func makeClient(tokenSink: ((String) -> Void)? = nil) -> HTTPAuthApiClient {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [URLProtocolStub.self]
        return HTTPAuthApiClient(baseURL: "https://test.local", configuration: cfg, tokenSink: tokenSink)
    }

    // MARK: JWT

    func testJWTExpiryAndPlayerId() {
        let token = Self.makeJWT(exp: 9_999_999_999, playerId: "abc")
        XCTAssertEqual(JWT.expiry(token), 9_999_999_999)
        XCTAssertEqual(JWT.playerId(token), "abc")
        XCTAssertFalse(JWT.isExpired(token))
    }

    func testJWTExpiredAndMalformedFailClosed() {
        XCTAssertTrue(JWT.isExpired(Self.makeJWT(exp: 1)))
        XCTAssertTrue(JWT.isExpired("not-a-jwt"))
        XCTAssertTrue(JWT.isExpired("only.two"))
    }

    // MARK: DTOs

    func testMeResponseLenientDefaults() throws {
        let me = try APIJSON.decode(MeResponse.self, from: Data(#"{"id":"x","email":"e@x.com"}"#.utf8))
        XCTAssertEqual(me.rating, 0)
        XCTAssertEqual(me.confidence, 0)
        XCTAssertEqual(me.trainingXp, 0)
        XCTAssertTrue(me.skillVector.isEmpty)
    }

    func testUpdateMeRequestOmitsNilFields() throws {
        let data = try APIJSON.encode(UpdateMeRequest(rating: 1500, confidence: nil))
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertNotNil(obj["rating"])
        XCTAssertNil(obj["confidence"])
    }

    func testLoginResponseDecodes() throws {
        let resp = try APIJSON.decode(LoginResponse.self, from: Self.loginResponseJSON(token: "tok"))
        XCTAssertEqual(resp.accessToken, "tok")
        XCTAssertEqual(resp.playerId, "pid")
        XCTAssertEqual(resp.tokenType, "bearer")
    }

    // MARK: Repository

    func testRepositoryStateTransitions() {
        let repo = AuthRepository(storage: InMemoryTokenStorage())
        XCTAssertFalse(repo.isLoggedIn)

        let valid = Self.makeJWT(exp: Int(Date().timeIntervalSince1970) + 3600, playerId: "pid")
        repo.saveToken(valid)
        guard case let .authenticated(token, pid) = repo.authState() else {
            return XCTFail("expected authenticated")
        }
        XCTAssertEqual(token, valid)
        XCTAssertEqual(pid, "pid")

        repo.saveToken(Self.makeJWT(exp: 1))   // expired -> unauthenticated
        XCTAssertEqual(repo.authState(), .unauthenticated)

        repo.clearToken()
        XCTAssertFalse(repo.isLoggedIn)
    }

    // MARK: API client (URLProtocol-mocked transport)

    func testLoginSuccessDecodesAndSendsApiVersion() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
             Self.loginResponseJSON(token: "tok"))
        }
        let result = await makeClient().login(email: "a@b.com", password: "pw")
        guard case let .success(login) = result else { return XCTFail("expected success: \(result)") }
        XCTAssertEqual(login.accessToken, "tok")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "X-API-Version"), "2")
    }

    func testLoginUnauthorizedMapsToHttpError() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 401, httpVersion: nil, headerFields: nil)!, Data())
        }
        let result = await makeClient().login(email: "a@b.com", password: "bad")
        guard case let .httpError(code) = result else { return XCTFail("expected httpError: \(result)") }
        XCTAssertEqual(code, 401)
    }

    func testRegisterAccepts201() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 201, httpVersion: nil, headerFields: nil)!,
             Self.loginResponseJSON(token: "tok"))
        }
        let result = await makeClient().register(email: "a@b.com", password: "pw")
        XCTAssertTrue(result.isSuccess)
    }

    func testMeConsumesAuthTokenRotation() async {
        var rotated: String?
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil,
                             headerFields: ["X-Auth-Token": "rotated"])!,
             Self.meResponseJSON())
        }
        let result = await makeClient(tokenSink: { rotated = $0 }).me(token: "old")
        XCTAssertTrue(result.isSuccess)
        XCTAssertEqual(rotated, "rotated")
    }

    func testUpdateMeSendsMethodOverrideAndBearer() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
             Self.meResponseJSON())
        }
        _ = await makeClient().updateMe(token: "t", rating: 1600, confidence: 0.7)
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "X-HTTP-Method-Override"), "PATCH")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer t")
    }

    // MARK: View model

    @MainActor
    private func makeViewModel(api: AuthApiClient) -> AuthViewModel {
        let defaults = UserDefaults(suiteName: "AuthVMTests")!
        defaults.removePersistentDomain(forName: "AuthVMTests")
        return AuthViewModel(api: api,
                             repository: AuthRepository(storage: InMemoryTokenStorage()),
                             defaults: defaults)
    }

    @MainActor
    func testViewModelLoginSuccessAuthenticates() async {
        let fake = FakeAuthApi()
        let token = Self.makeJWT(exp: Int(Date().timeIntervalSince1970) + 3600)
        fake.loginResult = .success(try! APIJSON.decode(LoginResponse.self, from: Self.loginResponseJSON(token: token)))
        let vm = makeViewModel(api: fake)
        await vm.login(email: "a@b.com", password: "pw")
        XCTAssertTrue(vm.authState.isAuthenticated)
        XCTAssertEqual(vm.phase, .idle)
    }

    @MainActor
    func testViewModelLoginFailureSetsError() async {
        let fake = FakeAuthApi()
        fake.loginResult = .httpError(401)
        let vm = makeViewModel(api: fake)
        await vm.login(email: "a@b.com", password: "bad")
        XCTAssertFalse(vm.authState.isAuthenticated)
        guard case .failed = vm.phase else { return XCTFail("expected failed phase") }
    }

    @MainActor
    func testViewModelSkipOnboardingSetsFlag() {
        let vm = makeViewModel(api: FakeAuthApi())
        XCTAssertFalse(vm.isOnboardingComplete)
        vm.skipOnboarding()
        XCTAssertTrue(vm.isOnboardingComplete)
    }

    @MainActor
    func testViewModelLogoutClearsAuth() async {
        let fake = FakeAuthApi()
        let token = Self.makeJWT(exp: Int(Date().timeIntervalSince1970) + 3600)
        fake.loginResult = .success(try! APIJSON.decode(LoginResponse.self, from: Self.loginResponseJSON(token: token)))
        let vm = makeViewModel(api: fake)
        await vm.login(email: "a@b.com", password: "pw")
        XCTAssertTrue(vm.authState.isAuthenticated)

        await vm.logout()
        XCTAssertFalse(vm.authState.isAuthenticated)
        XCTAssertTrue(fake.logoutCalled)
    }
}
