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

/// The coach's post-game action (`coach_action`). `type` is "NONE" when the
/// controller didn't fire; the summary then shows just the result + content.
struct CoachAction: Decodable, Equatable {
    let type: String
    let weakness: String?
    let reason: String?

    init(type: String = "NONE", weakness: String? = nil, reason: String? = nil) {
        self.type = type; self.weakness = weakness; self.reason = reason
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = (try? c.decode(String.self, forKey: .type)) ?? "NONE"
        weakness = try? c.decode(String.self, forKey: .weakness)
        reason = try? c.decode(String.self, forKey: .reason)
    }
    private enum CodingKeys: String, CodingKey { case type, weakness, reason }

    static let none = CoachAction()
    var hasContent: Bool { type.uppercased() != "NONE" && !type.isEmpty }
}

/// The coach's post-game plan copy (`coach_content`).
struct CoachContent: Decodable, Equatable {
    let title: String
    let description: String

    init(title: String = "", description: String = "") {
        self.title = title; self.description = description
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        description = (try? c.decode(String.self, forKey: .description)) ?? ""
    }
    private enum CodingKeys: String, CodingKey { case title, description }

    static let empty = CoachContent()
}

/// The game's worst player move (`biggest_mistake`), for the "Replay your mistake"
/// CTA. Present only when a move cleared the server's mistake threshold (150cp).
struct BiggestMistake: Decodable, Equatable {
    let fen: String          // position BEFORE the bad move (player to move)
    let playedMove: String   // the UCI the player actually played
    let moveNumber: Int
    let evalLossCp: Int

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        fen = (try? c.decode(String.self, forKey: .fen)) ?? ""
        playedMove = (try? c.decode(String.self, forKey: .playedMove)) ?? ""
        moveNumber = (try? c.decode(Int.self, forKey: .moveNumber)) ?? 0
        evalLossCp = (try? c.decode(Int.self, forKey: .evalLossCp)) ?? 0
    }
    private enum CodingKeys: String, CodingKey { case fen, playedMove, moveNumber, evalLossCp }

    /// A blank/zero payload (or no FEN) means "nothing worth replaying".
    var isReplayable: Bool { !fen.isEmpty && evalLossCp > 0 }
}

/// Response from POST /game/finish. Models the post-game-summary fields (the
/// coach plan + the biggest mistake); the rest of the large payload is ignored.
/// The rating/confidence numbers are decoded but never shown (Elo is hidden).
struct GameFinishResponse: Decodable {
    let status: String
    let newRating: Double
    let coachAction: CoachAction
    let coachContent: CoachContent
    let biggestMistake: BiggestMistake?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = (try? c.decode(String.self, forKey: .status)) ?? "stored"
        newRating = (try? c.decode(Double.self, forKey: .newRating)) ?? 0
        coachAction = (try? c.decode(CoachAction.self, forKey: .coachAction)) ?? .none
        coachContent = (try? c.decode(CoachContent.self, forKey: .coachContent)) ?? .empty
        biggestMistake = try? c.decode(BiggestMistake.self, forKey: .biggestMistake)
    }
    private enum CodingKeys: String, CodingKey {
        case status, newRating, coachAction, coachContent, biggestMistake
    }
}
