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

/// Response from GET /coach/plan/today (or JSON `null` when no active plan).
struct TodayPlan: Decodable, Equatable {
    let theme: String
    let verdict: String
    let totalDays: Int
    let todayPuzzle: TodayPuzzle?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        theme = (try? c.decode(String.self, forKey: .theme)) ?? ""
        verdict = (try? c.decode(String.self, forKey: .verdict)) ?? ""
        totalDays = (try? c.decode(Int.self, forKey: .totalDays)) ?? 3
        todayPuzzle = try? c.decode(TodayPuzzle.self, forKey: .todayPuzzle)
    }
    private enum CodingKeys: String, CodingKey { case theme, verdict, totalDays, todayPuzzle }

    /// A card is shown only when a puzzle is actually due.
    var hasDuePuzzle: Bool { todayPuzzle != nil }
}
