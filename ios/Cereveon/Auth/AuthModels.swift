import Foundation

// MARK: - Requests
// (`device_info` is sent as "ios"; the Android client sends "android".)

struct LoginRequest: Encodable {
    let email: String
    let password: String
    var deviceInfo: String = "ios"
}

struct RegisterRequest: Encodable {
    let email: String
    let password: String
    var deviceInfo: String = "ios"
}

struct ChangePasswordRequest: Encodable {
    let currentPassword: String
    let newPassword: String
}

/// PATCH /auth/me — at least one field must be non-nil server-side. Nil fields
/// are omitted on the wire (JSONEncoder skips nil optionals), so the backend
/// sees exactly the keys the client meant to update.
struct UpdateMeRequest: Encodable {
    let rating: Double?
    let confidence: Double?
}

// MARK: - Responses

/// Response from POST /auth/login and POST /auth/register.
struct LoginResponse: Decodable {
    let accessToken: String
    let playerId: String
    let tokenType: String

    private enum CodingKeys: String, CodingKey { case accessToken, playerId, tokenType }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        accessToken = try c.decode(String.self, forKey: .accessToken)
        playerId = try c.decode(String.self, forKey: .playerId)
        tokenType = try c.decodeIfPresent(String.self, forKey: .tokenType) ?? "bearer"
    }
}

/// Response from GET /auth/me. Lenient like the Android `coerceInputValues`
/// config: a missing or null field falls back to its default rather than
/// failing the whole decode.
struct MeResponse: Decodable {
    let id: String
    let email: String
    let rating: Double
    let confidence: Double
    let skillVector: [String: Double]
    let trainingXp: Int

    private enum CodingKeys: String, CodingKey {
        case id, email, rating, confidence, skillVector, trainingXp
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? ""
        email = (try? c.decode(String.self, forKey: .email)) ?? ""
        rating = (try? c.decode(Double.self, forKey: .rating)) ?? 0
        confidence = (try? c.decode(Double.self, forKey: .confidence)) ?? 0
        skillVector = (try? c.decode([String: Double].self, forKey: .skillVector)) ?? [:]
        trainingXp = (try? c.decode(Int.self, forKey: .trainingXp)) ?? 0
    }
}

// MARK: - Auth state

enum AuthState: Equatable {
    case authenticated(token: String, playerId: String)
    case unauthenticated

    var isAuthenticated: Bool {
        if case .authenticated = self { return true }
        return false
    }
}

// MARK: - JWT (client-side only; the server is authoritative)

/// Reads JWT claims without signature validation. Mirrors the Android
/// `parseJwtExpiry` / `parseJwtPlayerId` / `isJwtExpired` helpers — fail-closed:
/// a malformed/unsigned token is treated as expired so the client never sends a
/// known-dead token.
enum JWT {
    static func expiry(_ token: String) -> Int? {
        guard let payload = payloadJSON(token) else { return nil }
        if let n = payload["exp"] as? Int { return n }
        if let d = payload["exp"] as? Double { return Int(d) }
        return nil
    }

    static func playerId(_ token: String) -> String? {
        payloadJSON(token)?["player_id"] as? String
    }

    static func isExpired(_ token: String, now: Date = Date()) -> Bool {
        guard let exp = expiry(token) else { return true }
        return Int(now.timeIntervalSince1970) >= exp
    }

    private static func payloadJSON(_ token: String) -> [String: Any]? {
        let parts = token.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3, let data = base64URLDecode(String(parts[1])) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    private static func base64URLDecode(_ s: String) -> Data? {
        var b = s.replacingOccurrences(of: "-", with: "+")
                 .replacingOccurrences(of: "_", with: "/")
        let remainder = b.count % 4
        if remainder > 0 { b += String(repeating: "=", count: 4 - remainder) }
        return Data(base64Encoded: b)
    }
}
