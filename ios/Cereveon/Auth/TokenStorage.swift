import Foundation
import Security

/// Abstraction over JWT persistence. Defining it as a protocol lets
/// `AuthRepository` be tested with a pure in-memory fake (mirrors the Android
/// `TokenStorage` interface). Production wires in `KeychainTokenStorage`.
protocol TokenStorage {
    func save(_ token: String)
    func load() -> String?
    func clear()
}

/// Production storage backed by the iOS Keychain — the equivalent of Android's
/// EncryptedSharedPreferences + Keystore. The JWT is a device-only generic
/// password, readable after first unlock (so a relaunch/background refresh can
/// reach it) but never synced to iCloud or included in backups.
final class KeychainTokenStorage: TokenStorage {
    private let service: String
    private let account: String

    init(service: String = "ai.chesscoach.app.auth", account: String = "jwt_token") {
        self.service = service
        self.account = account
    }

    private var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    func save(_ token: String) {
        guard let data = token.data(using: .utf8) else { return }
        // Delete-then-add is the simplest idempotent upsert for a single item.
        SecItemDelete(baseQuery as CFDictionary)
        var attributes = baseQuery
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(attributes as CFDictionary, nil)
    }

    func load() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func clear() {
        SecItemDelete(baseQuery as CFDictionary)
    }
}

/// In-memory storage for tests and previews (mirrors the Android in-memory fake).
final class InMemoryTokenStorage: TokenStorage {
    private var token: String?
    init(token: String? = nil) { self.token = token }
    func save(_ token: String) { self.token = token }
    func load() -> String? { token }
    func clear() { token = nil }
}
