import Foundation

/// Shared JSON coder for the API clients, mirroring the Android `ApiJson`:
/// snake_case wire format (auto-converted to/from camelCase properties), unknown
/// response keys ignored (the JSONDecoder default), and nil fields omitted on
/// encode (the JSONEncoder default — matches `encodeDefaults = false`).
enum APIJSON {
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    static func encode<T: Encodable>(_ value: T) throws -> Data {
        try encoder.encode(value)
    }

    static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try decoder.decode(type, from: data)
    }
}
