import Foundation

// Coach-chat wire models — POST /chat, POST /chat/stream (same request shape),
// and GET /chat/history. Faithful port of the Android `CoachApiModels.kt` DTOs.
// `APIJSON` carries snake_case<->camelCase both ways, so the Swift property
// names are camelCase and the CodingKeys stay camelCase (the converter maps the
// wire `player_profile` / `engine_signal` / `created_at` for us).

// MARK: - Request

/// One message in the conversation history (most-recent last). `role` is
/// "user" or "assistant"; the backend text field is `content`, not `text`.
struct ChatMessageDTO: Codable, Equatable {
    let role: String
    let content: String
}

/// Optional per-request player context for personalised coaching (Android sources
/// it from the latest `/game/finish` rating). The iOS client does not source it
/// yet, but the shape is modelled so it can be sent without a wire change.
struct PlayerProfileDTO: Encodable, Equatable {
    let rating: Double
    let confidence: Double
}

/// Request body for POST /chat and POST /chat/stream (identical wire shape).
/// Optional fields are omitted when nil — Swift synthesises `encodeIfPresent`
/// for optionals — matching the Android `encodeDefaults = false` behaviour.
struct ChatRequest: Encodable {
    let fen: String
    let messages: [ChatMessageDTO]
    var playerProfile: PlayerProfileDTO? = nil
    var pastMistakes: [String]? = nil
    var moveCount: Int? = nil
    var coachVoice: String? = nil
    var gameId: String? = nil
    var lastMove: String? = nil
}

/// Fire-and-forget thumbs-up / thumbs-down for the latest coaching reply at a
/// position. POST /game/coach-feedback. `sessionFen` must be a valid 6-field FEN
/// (or "startpos") — the server runs a FEN validator on it. APIJSON snake-cases
/// the fields to `session_fen` / `is_helpful`.
struct CoachFeedbackRequest: Encodable {
    let sessionFen: String
    let isHelpful: Bool
}

// MARK: - Response (POST /chat)

/// Coarse centipawn band for the context header (never a raw number). Lenient.
struct EvaluationDTO: Decodable, Equatable {
    let band: String?
    let side: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        band = try? c.decode(String.self, forKey: .band)
        side = try? c.decode(String.self, forKey: .side)
    }

    private enum CodingKeys: String, CodingKey { case band, side }
}

/// Engine context attached to a /chat response (and the stream `done`/`abort`).
struct EngineSignalDTO: Decodable, Equatable {
    let evaluation: EvaluationDTO?
    let phase: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        evaluation = try? c.decode(EvaluationDTO.self, forKey: .evaluation)
        phase = try? c.decode(String.self, forKey: .phase)
    }

    private enum CodingKeys: String, CodingKey { case evaluation, phase }
}

/// Response from POST /chat. Lenient: a missing `reply` decodes to "".
struct ChatResponse: Decodable {
    let reply: String
    let engineSignal: EngineSignalDTO?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        reply = (try? c.decode(String.self, forKey: .reply)) ?? ""
        engineSignal = try? c.decode(EngineSignalDTO.self, forKey: .engineSignal)
    }

    private enum CodingKeys: String, CodingKey { case reply, engineSignal }
}

// MARK: - History (GET /chat/history)

/// One persisted chat turn. Roles are "user" or "assistant"; `mode` defaults to
/// "CHAT_V1". Lenient so a future server field never breaks the seed.
struct ChatHistoryTurnDTO: Decodable, Equatable {
    let id: String
    let role: String
    let content: String
    let fen: String?
    let mode: String
    let createdAt: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? ""
        role = (try? c.decode(String.self, forKey: .role)) ?? ""
        content = (try? c.decode(String.self, forKey: .content)) ?? ""
        fen = try? c.decode(String.self, forKey: .fen)
        mode = (try? c.decode(String.self, forKey: .mode)) ?? "CHAT_V1"
        createdAt = try? c.decode(String.self, forKey: .createdAt)
    }

    private enum CodingKeys: String, CodingKey { case id, role, content, fen, mode, createdAt }
}

/// Response from GET /chat/history (turns chronological, oldest first). Empty
/// list when the player has no persisted history yet.
struct ChatHistoryResponse: Decodable {
    let turns: [ChatHistoryTurnDTO]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        turns = (try? c.decode([ChatHistoryTurnDTO].self, forKey: .turns)) ?? []
    }

    private enum CodingKeys: String, CodingKey { case turns }
}

// MARK: - Streaming (POST /chat/stream)

/// One Server-Sent Event from POST /chat/stream. Mirrors the Android
/// `StreamChunk` sealed type. The terminal event is `done` (clean close) or
/// `abort` (validate-before-emit aborted — render `reply` IN PLACE of any
/// partial). `error` carries a transport/HTTP failure.
enum ChatStreamEvent: Equatable {
    case chunk(String)
    case done(engineSignal: EngineSignalDTO?, mode: String)
    case abort(reply: String, engineSignal: EngineSignalDTO?, mode: String)
    case error(String)

    /// Decode one SSE `data:` payload (the JSON after the `data:` prefix) into an
    /// event. Tolerant of unknown `type` values and malformed JSON (→ nil, which
    /// the caller drops). Mirrors `HttpCoachApiClient.parseStreamChunk`.
    static func parse(_ payload: String) -> ChatStreamEvent? {
        guard let data = payload.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = root["type"] as? String
        else { return nil }

        switch type {
        case "chunk":
            return .chunk(root["text"] as? String ?? "")
        case "done":
            return .done(engineSignal: decodeSignal(root["engine_signal"]),
                         mode: root["mode"] as? String ?? "CHAT_V1")
        case "abort":
            return .abort(reply: root["reply"] as? String ?? "",
                          engineSignal: decodeSignal(root["engine_signal"]),
                          mode: root["mode"] as? String ?? "CHAT_V1")
        case "error":
            return .error(root["message"] as? String ?? "Server error")
        default:
            return nil
        }
    }

    /// Re-encode the nested `engine_signal` object and decode it through `APIJSON`
    /// so the same lenient `EngineSignalDTO` rules apply. Null/absent → nil.
    private static func decodeSignal(_ raw: Any?) -> EngineSignalDTO? {
        guard let dict = raw as? [String: Any],
              let data = try? JSONSerialization.data(withJSONObject: dict)
        else { return nil }
        return try? APIJSON.decode(EngineSignalDTO.self, from: data)
    }
}
