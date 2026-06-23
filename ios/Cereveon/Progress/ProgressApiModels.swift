import Foundation

// Models for GET /player/progress. Decoded with a PLAIN JSONDecoder (no
// snake→camel conversion) on purpose: `skill_vector` / `category_scores` are
// dictionaries whose keys (e.g. "tactical_vision") must stay literal, and
// `.convertFromSnakeCase` would rewrite dictionary keys too. So each struct
// declares explicit snake_case CodingKeys instead.
//
// Only the fields the dashboard renders are modelled. Per the Android
// ProgressDashboard (post-Elo-removal), the rating / confidence / opponent-Elo /
// sparkline are deliberately NOT shown, so `history` and the raw rating are
// omitted here.

/// One prioritised training recommendation.
struct ProgressRecommendation: Decodable, Equatable {
    let category: String
    let priority: String   // "high" | "medium" | "low"
    let rationale: String

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        category = (try? c.decode(String.self, forKey: .category)) ?? ""
        priority = (try? c.decode(String.self, forKey: .priority)) ?? ""
        rationale = (try? c.decode(String.self, forKey: .rationale)) ?? ""
    }

    enum CodingKeys: String, CodingKey { case category, priority, rationale }
}

/// Historical-analysis block: per-category scores + the prioritised plan.
struct ProgressAnalysis: Decodable {
    let dominantCategory: String?
    let categoryScores: [String: Double]
    let recommendations: [ProgressRecommendation]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dominantCategory = try? c.decode(String.self, forKey: .dominantCategory)
        categoryScores = (try? c.decode([String: Double].self, forKey: .categoryScores)) ?? [:]
        recommendations = (try? c.decode([ProgressRecommendation].self, forKey: .recommendations)) ?? []
    }

    enum CodingKeys: String, CodingKey {
        case dominantCategory = "dominant_category"
        case categoryScores = "category_scores"
        case recommendations
    }
}

/// Live world-model snapshot. Rating/confidence are intentionally not modelled
/// (hidden in the UI).
struct ProgressCurrent: Decodable {
    let tier: String                  // beginner | intermediate | advanced
    let teachingStyle: String         // simple | intermediate | advanced
    let explanationDepth: Double      // 0…1
    let conceptComplexity: Double     // 0…1
    let skillVector: [String: Double] // fallback for the weakness chart

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tier = (try? c.decode(String.self, forKey: .tier)) ?? "intermediate"
        teachingStyle = (try? c.decode(String.self, forKey: .teachingStyle)) ?? ""
        explanationDepth = (try? c.decode(Double.self, forKey: .explanationDepth)) ?? 0
        conceptComplexity = (try? c.decode(Double.self, forKey: .conceptComplexity)) ?? 0
        skillVector = (try? c.decode([String: Double].self, forKey: .skillVector)) ?? [:]
    }

    enum CodingKeys: String, CodingKey {
        case tier
        case teachingStyle = "teaching_style"
        case explanationDepth = "explanation_depth"
        case conceptComplexity = "concept_complexity"
        case skillVector = "skill_vector"
    }
}

/// Top-level GET /player/progress response (the subset the dashboard uses).
struct PlayerProgressResponse: Decodable {
    let current: ProgressCurrent
    let analysis: ProgressAnalysis
}
