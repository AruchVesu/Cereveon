import Foundation

/// Manages the JWT lifecycle, wrapping `TokenStorage` with semantic operations.
/// Mirrors the Android `AuthRepository`: `authState` combines a presence check
/// with a fail-closed expiry check; `isJwtExpired` is a client-side optimisation
/// only — the server always validates authoritatively.
///
/// Stateless aside from the injected storage, so it is safe to call from the
/// token-rotation sink (a background context) and the main thread; the Keychain
/// backing is itself thread-safe.
final class AuthRepository {
    private let storage: TokenStorage

    init(storage: TokenStorage) {
        self.storage = storage
    }

    /// Persist `token`. A blank token is ignored (it can never come from a 2xx
    /// response); the Android equivalent throws, but a no-op keeps callers simple.
    func saveToken(_ token: String) {
        guard !token.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        storage.save(token)
    }

    func getToken() -> String? { storage.load() }

    func authState() -> AuthState {
        guard let token = getToken() else { return .unauthenticated }
        if JWT.isExpired(token) { return .unauthenticated }
        return .authenticated(token: token, playerId: JWT.playerId(token) ?? "")
    }

    var isLoggedIn: Bool { authState().isAuthenticated }

    func clearToken() { storage.clear() }
}
