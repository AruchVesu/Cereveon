import Foundation
import Combine

/// Drives the auth + onboarding UI. Owns the `AuthApiClient` + `AuthRepository`,
/// exposes the observable `authState` / `isOnboardingComplete` / `phase`, and
/// turns intents (login, register, calibrate, logout) into state transitions.
@MainActor
final class AuthViewModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        case working
        case failed(String)
    }

    @Published private(set) var authState: AuthState
    @Published private(set) var isOnboardingComplete: Bool
    @Published private(set) var phase: Phase = .idle

    /// The current Bearer JWT, or nil when unauthenticated. Read by the play-loop
    /// API clients (live coaching / eval / game persistence) for `Authorization`.
    var bearerToken: String? {
        if case let .authenticated(token, _) = authState { return token }
        return nil
    }

    private let api: AuthApiClient
    private let repository: AuthRepository
    private let defaults: UserDefaults

    private static let onboardingKey = "ai.chesscoach.onboardingComplete"

    init() {
        let repo = AuthRepository(storage: KeychainTokenStorage())
        // Rotate the stored JWT whenever the backend hands back X-Auth-Token.
        self.api = HTTPAuthApiClient(
            delegate: PinningURLSessionDelegate(),
            tokenSink: { token in repo.saveToken(token) }
        )
        self.repository = repo
        self.defaults = .standard
        self.authState = repo.authState()
        self.isOnboardingComplete = UserDefaults.standard.bool(forKey: Self.onboardingKey)
    }

    /// Test / preview seam.
    init(api: AuthApiClient, repository: AuthRepository, defaults: UserDefaults) {
        self.api = api
        self.repository = repository
        self.defaults = defaults
        self.authState = repository.authState()
        self.isOnboardingComplete = defaults.bool(forKey: Self.onboardingKey)
    }

    func login(email: String, password: String) async {
        await authenticate { await self.api.login(email: email, password: password) }
    }

    func register(email: String, password: String) async {
        await authenticate { await self.api.register(email: email, password: password) }
    }

    private func authenticate(_ call: () async -> APIResult<LoginResponse>) async {
        phase = .working
        switch await call() {
        case let .success(response):
            repository.saveToken(response.accessToken)
            authState = repository.authState()
            phase = .idle
        case let .httpError(code):
            phase = .failed(Self.message(for: code))
        case .timeout:
            phase = .failed("The request timed out. Check your connection and try again.")
        case .networkError:
            phase = .failed("Couldn't reach the coach. Check your connection and try again.")
        }
    }

    /// Send the onboarding calibration estimate. Does NOT finalize onboarding —
    /// the Complete screen's "Start" (`skipOnboarding`) is the finalize point,
    /// mirroring Android's OnboardingCompleteActivity.
    func submitCalibration(rating: Double, confidence: Double) async {
        guard case let .authenticated(token, _) = authState else { return }
        phase = .working
        switch await api.updateMe(token: token, rating: rating, confidence: confidence) {
        case .success:
            authState = repository.authState()   // token may have rotated
            phase = .idle
        case let .httpError(code):
            phase = .failed(Self.message(for: code))
        case .timeout:
            phase = .failed("The request timed out. Try again.")
        case .networkError:
            phase = .failed("Couldn't save your calibration. Try again.")
        }
    }

    /// Finalize onboarding — both the Calibration "Skip" and the Complete
    /// "Start" land here, flipping the local flag so RootView routes to the app.
    func skipOnboarding() {
        defaults.set(true, forKey: Self.onboardingKey)
        isOnboardingComplete = true
    }

    func logout() async {
        if case let .authenticated(token, _) = authState {
            _ = await api.logout(token: token)   // best-effort server-side invalidation
        }
        repository.clearToken()
        authState = .unauthenticated
        phase = .idle
    }

    /// Change the signed-in user's password. Returns `nil` on success, else a
    /// user-facing error message. The token may rotate on success.
    func changePassword(current: String, new: String) async -> String? {
        guard case let .authenticated(token, _) = authState else { return "You're signed out." }
        switch await api.changePassword(currentPassword: current, newPassword: new, token: token) {
        case .success:
            authState = repository.authState()
            return nil
        case let .httpError(code):
            switch code {
            case 401, 403: return "Your current password is incorrect."
            case 400, 422: return "Please check your passwords and try again."
            case 429: return "Too many attempts. Please wait a moment."
            default: return "Something went wrong (error \(code)). Please try again."
            }
        case .timeout:
            return "The request timed out. Try again."
        case .networkError:
            return "Couldn't reach the server. Try again."
        }
    }

    func clearError() {
        if case .failed = phase { phase = .idle }
    }

    private static func message(for code: Int) -> String {
        switch code {
        case 401: return "Incorrect email or password."
        case 409: return "That email is already registered."
        case 400, 422: return "Please check your details and try again."
        case 429: return "Too many attempts. Please wait a moment."
        default: return "Something went wrong (error \(code)). Please try again."
        }
    }
}
