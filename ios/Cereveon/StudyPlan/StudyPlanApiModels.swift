import Foundation

/// The due puzzle in the active per-mistake study plan (`today_puzzle`).
/// `dayOffset` ∈ {0, 3, 7} → "Day 1/2/3". Decoded via APIJSON.
struct TodayPuzzle: Decodable, Equatable {
    let dayOffset: Int
    let fen: String
    let expectedMoveUci: String

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dayOffset = (try? c.decode(Int.self, forKey: .dayOffset)) ?? 0
        fen = (try? c.decode(String.self, forKey: .fen)) ?? ""
        expectedMoveUci = (try? c.decode(String.self, forKey: .expectedMoveUci)) ?? ""
    }
    private enum CodingKeys: String, CodingKey { case dayOffset, fen, expectedMoveUci }

    /// 1-based day in the plan (offset 0→1, 3→2, 7→3).
    var dayNumber: Int { [0: 1, 3: 2, 7: 3][dayOffset] ?? 1 }
}

/// One day-slot in the week-overview schedule (`days[]`).
///
/// Powers the overview screen: each day is `completed` (done), `isDue`
/// (available now), or neither (locked behind its `dueAt`).  Unlike
/// `TodayPuzzle` this carries no FEN / expected move — the playable
/// position comes from `TodayPlan.todayPuzzle`. Decoded via APIJSON.
struct PlanDay: Decodable, Equatable {
    let dayOffset: Int
    let dueAt: String
    let completed: Bool
    let isDue: Bool
    let sourceType: String

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dayOffset = (try? c.decode(Int.self, forKey: .dayOffset)) ?? 0
        dueAt = (try? c.decode(String.self, forKey: .dueAt)) ?? ""
        completed = (try? c.decode(Bool.self, forKey: .completed)) ?? false
        isDue = (try? c.decode(Bool.self, forKey: .isDue)) ?? false
        sourceType = (try? c.decode(String.self, forKey: .sourceType)) ?? ""
    }
    private enum CodingKeys: String, CodingKey {
        case dayOffset, dueAt, completed, isDue, sourceType
    }

    /// 1-based day in the plan (offset 0→1, 3→2, 7→3).
    var dayNumber: Int { [0: 1, 3: 2, 7: 3][dayOffset] ?? 1 }
}

/// Response from GET /coach/plan/today (or JSON `null` when no active plan).
/// Also the body of POST /coach/plan/puzzle/complete.
struct TodayPlan: Decodable, Equatable {
    let theme: String
    let verdict: String
    /// Aggregate dominant weakness the week is built around — one of
    /// opening_preparation / tactical_vision / positional_play /
    /// endgame_technique, or nil for legacy plans / too little history.
    let anchorCategory: String?
    /// "active" while in progress, "completed" once every day is solved.
    let status: String
    let totalDays: Int
    let todayPuzzle: TodayPuzzle?
    /// The full week schedule, ordered by day_offset (empty only when
    /// decoding an older server response that predates the field).
    let days: [PlanDay]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        theme = (try? c.decode(String.self, forKey: .theme)) ?? ""
        verdict = (try? c.decode(String.self, forKey: .verdict)) ?? ""
        anchorCategory = try? c.decode(String.self, forKey: .anchorCategory)
        status = (try? c.decode(String.self, forKey: .status)) ?? "active"
        totalDays = (try? c.decode(Int.self, forKey: .totalDays)) ?? 3
        todayPuzzle = try? c.decode(TodayPuzzle.self, forKey: .todayPuzzle)
        days = (try? c.decode([PlanDay].self, forKey: .days)) ?? []
    }
    private enum CodingKeys: String, CodingKey {
        case theme, verdict, anchorCategory, status, totalDays, todayPuzzle, days
    }

    /// A card is shown only when a puzzle is actually due.
    var hasDuePuzzle: Bool { todayPuzzle != nil }
}
