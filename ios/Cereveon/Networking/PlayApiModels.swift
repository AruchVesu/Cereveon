import Foundation

// MARK: - /engine/eval

struct EngineEvalRequest: Encodable {
    let fen: String
}

/// Response from POST /engine/eval. `score` is centipawns from White's POV
/// (positive = White ahead; ±10000 = mate); nil when the engine pool is down.
/// `bestMove` "" is folded to nil (mirrors the Android empty-string sentinel).
struct EngineEvalResponse: Decodable {
    let score: Int?
    let bestMove: String?
    let source: String

    private enum CodingKeys: String, CodingKey { case score, bestMove, source }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        score = try c.decodeIfPresent(Int.self, forKey: .score)
        let raw = try c.decodeIfPresent(String.self, forKey: .bestMove)
        bestMove = (raw?.isEmpty == true) ? nil : raw
        source = (try? c.decode(String.self, forKey: .source)) ?? "engine"
    }
}

// MARK: - /live/move

struct LiveMoveRequest: Encodable {
    let fen: String
    let uci: String
    var playerId: String = "ios"
    let fenBefore: String?   // nil is omitted on the wire → server move_quality "unknown"
}

/// Response from POST /live/move. The `engine_signal` ESV is intentionally not
/// modelled here — the dock uses `hint` + `moveQuality`, and the eval band uses
/// the /engine/eval score. Lenient decode (missing/null → defaults).
struct LiveMoveResponse: Decodable {
    let status: String
    let hint: String
    let moveQuality: String
    let mode: String

    private enum CodingKeys: String, CodingKey { case status, hint, moveQuality, mode }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = (try? c.decode(String.self, forKey: .status)) ?? "ok"
        hint = (try? c.decode(String.self, forKey: .hint)) ?? ""
        moveQuality = (try? c.decode(String.self, forKey: .moveQuality)) ?? "unknown"
        mode = (try? c.decode(String.self, forKey: .mode)) ?? "LIVE_V1"
    }
}

// MARK: - /game/start, /game/finish

struct GameStartRequest: Encodable {
    let playerId: String   // accepted for back-compat; the server derives identity from the JWT
}

struct GameStartResponse: Decodable {
    let gameId: String
    private enum CodingKeys: String, CodingKey { case gameId }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        gameId = (try? c.decode(String.self, forKey: .gameId)) ?? ""
    }
}

struct GameFinishRequest: Encodable {
    let pgn: String
    let result: String          // "win" | "loss" | "draw"
    let accuracy: Double        // 0…1 (server recomputes; this is a fallback)
    var weaknesses: [String: Double] = [:]
    var playerId: String? = nil
    var gameId: String? = nil   // ties back to the /game/start row
}

/// Response from POST /game/finish. Only the fields the client needs are
/// modelled; the rest of the (large) payload is ignored.
struct GameFinishResponse: Decodable {
    let status: String
    let newRating: Double
    private enum CodingKeys: String, CodingKey { case status, newRating }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = (try? c.decode(String.self, forKey: .status)) ?? "stored"
        newRating = (try? c.decode(Double.self, forKey: .newRating)) ?? 0
    }
}
