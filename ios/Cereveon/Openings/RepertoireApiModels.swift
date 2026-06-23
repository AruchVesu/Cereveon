import Foundation

// Models for the opening repertoire (GET /repertoire, POST /repertoire,
// DELETE /repertoire/{eco}, POST /repertoire/{eco}/active,
// POST /repertoire/{eco}/drill-result). All editing endpoints return the full
// updated {openings} list. APIJSON (convertFromSnakeCase) is safe — no dict
// fields.

/// One opening line in the player's repertoire. `mastery` is 0…1 (doubles as the
/// bar fraction); `line` is a SAN sequence that may use chess glyphs (♘f3).
struct RepertoireOpening: Decodable, Equatable, Identifiable {
    let eco: String
    let name: String
    let line: String
    let mastery: Double
    let isActive: Bool
    let ordinal: Int

    var id: String { eco }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        eco = (try? c.decode(String.self, forKey: .eco)) ?? ""
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        line = (try? c.decode(String.self, forKey: .line)) ?? ""
        mastery = (try? c.decode(Double.self, forKey: .mastery)) ?? 0
        isActive = (try? c.decode(Bool.self, forKey: .isActive)) ?? false
        ordinal = (try? c.decode(Int.self, forKey: .ordinal)) ?? 0
    }

    private enum CodingKeys: String, CodingKey {
        case eco, name, line, mastery, isActive, ordinal
    }
}

struct RepertoireResponse: Decodable {
    let openings: [RepertoireOpening]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        openings = (try? c.decode([RepertoireOpening].self, forKey: .openings)) ?? []
    }

    private enum CodingKeys: String, CodingKey { case openings }
}

/// Body for POST /repertoire (upsert by eco). Mastery is omitted → server default.
struct RepertoireAddRequest: Encodable {
    let eco: String
    let name: String
    let line: String
}

/// Body for POST /repertoire/{eco}/drill-result. `outcome` ∈ [0, 1].
struct DrillResultRequest: Encodable {
    let outcome: Double
}
