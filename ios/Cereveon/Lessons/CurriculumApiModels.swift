import Foundation

/// Response from POST /curriculum/next — the next recommended study focus. It is
/// a study-plan descriptor (topic / exercise type / difficulty / session length)
/// plus the same training recommendations the progress dashboard shows; it is NOT
/// a concrete puzzle (no FEN / solution), so Lessons is a recommendation card, not
/// an on-board solver. `session_minutes` lives inside the nested `payload`.
/// Decoded via APIJSON (convertFromSnakeCase) — no dictionary fields.
struct CurriculumNext: Decodable, Equatable {
    let topic: String
    let difficulty: String
    let exerciseType: String
    let sessionMinutes: Int
    let recommendations: [ProgressRecommendation]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        topic = (try? c.decode(String.self, forKey: .topic)) ?? ""
        difficulty = Self.flexibleString(c, .difficulty)
        exerciseType = (try? c.decode(String.self, forKey: .exerciseType)) ?? ""
        recommendations = (try? c.decode([ProgressRecommendation].self, forKey: .recommendations)) ?? []
        if let payload = try? c.nestedContainer(keyedBy: PayloadKeys.self, forKey: .payload) {
            sessionMinutes = (try? payload.decode(Int.self, forKey: .sessionMinutes)) ?? 0
        } else {
            sessionMinutes = 0
        }
    }

    /// `difficulty` may be a string ("intermediate") or a numeric level on the
    /// wire; accept either.
    private static func flexibleString(_ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys) -> String {
        if let s = try? c.decode(String.self, forKey: key) { return s }
        if let i = try? c.decode(Int.self, forKey: key) { return String(i) }
        return ""
    }

    private enum CodingKeys: String, CodingKey {
        case topic, difficulty, exerciseType, recommendations, payload
    }
    private enum PayloadKeys: String, CodingKey {
        case sessionMinutes
    }
}
