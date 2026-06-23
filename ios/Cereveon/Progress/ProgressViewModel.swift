import Foundation
import Combine

/// One weakness-profile bar: a category label, its 0…1 score, and the priority
/// (from the recommendations) used to colour it.
struct WeaknessEntry: Identifiable, Equatable {
    let label: String
    let value: Double
    let priority: String
    var id: String { label }
}

/// One "how the coach sees you" row.
struct WorldModelRow: Identifiable, Equatable {
    let label: String
    let value: String
    var id: String { label }
}

/// One training-focus recommendation.
struct RecommendationRow: Identifiable, Equatable {
    let priority: String
    let category: String
    let rationale: String
    var id: String { category + "·" + priority }
}

/// Loads GET /player/progress and maps it to the dashboard's display models.
/// Rating/confidence are never surfaced (mirrors the Android post-Elo-removal
/// dashboard); only the weakness profile, world-model snapshot, and training
/// focus are shown.
@MainActor
final class ProgressViewModel: ObservableObject {
    enum State: Equatable {
        case loading
        case loaded(weaknesses: [WeaknessEntry], worldModel: [WorldModelRow], recommendations: [RecommendationRow])
        case empty
        case error
    }

    @Published private(set) var state: State = .loading

    private let client: ProgressClient
    private let token: () -> String?

    init(client: ProgressClient, token: @escaping () -> String?) {
        self.client = client
        self.token = token
    }

    func load() async {
        state = .loading
        guard let token = token() else { state = .error; return }
        switch await client.progress(token: token) {
        case let .success(data):
            let weaknesses = Self.weaknessEntries(from: data)
            let worldModel = Self.worldModelRows(from: data.current)
            let recommendations = Self.recommendationRows(from: data.analysis)
            state = (weaknesses.isEmpty && recommendations.isEmpty)
                ? .empty
                : .loaded(weaknesses: weaknesses, worldModel: worldModel, recommendations: recommendations)
        case .httpError, .timeout, .networkError:
            state = .error
        }
    }

    // MARK: - Pure mapping (mirrors ProgressDashboardBottomSheet)

    private static let categoryLabels: [String: String] = [
        "tactical_vision": "Tactics",
        "opening_preparation": "Opening",
        "endgame_technique": "Endgame",
        "positional_play": "Position",
    ]

    static func weaknessEntries(from data: PlayerProgressResponse) -> [WeaknessEntry] {
        let priorityByCategory = Dictionary(
            data.analysis.recommendations.map { ($0.category, $0.priority) },
            uniquingKeysWith: { first, _ in first }
        )
        // Prefer the pipeline's category_scores; fall back to the raw skill vector.
        let source = data.analysis.categoryScores.isEmpty ? data.current.skillVector : data.analysis.categoryScores
        return source
            .sorted { $0.value > $1.value }
            .map { category, score in
                WeaknessEntry(label: categoryLabels[category] ?? prettify(category),
                              value: score,
                              priority: priorityByCategory[category] ?? "")
            }
    }

    static func worldModelRows(from current: ProgressCurrent) -> [WorldModelRow] {
        [
            WorldModelRow(label: "Tier", value: tierLabel(current.tier)),
            WorldModelRow(label: "Coach style", value: styleLabel(current.teachingStyle)),
            WorldModelRow(label: "Depth", value: percent(current.explanationDepth)),
            WorldModelRow(label: "Complexity", value: percent(current.conceptComplexity)),
        ]
    }

    static func recommendationRows(from analysis: ProgressAnalysis) -> [RecommendationRow] {
        analysis.recommendations.map {
            RecommendationRow(priority: $0.priority,
                              category: categoryLabels[$0.category] ?? prettify($0.category),
                              rationale: $0.rationale)
        }
    }

    private static func prettify(_ key: String) -> String {
        key.split(separator: "_")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }

    private static func tierLabel(_ tier: String) -> String {
        switch tier {
        case "beginner": return "Beginner — keep it simple"
        case "intermediate": return "Intermediate — building concepts"
        case "advanced": return "Advanced — deep analysis"
        default: return tier.capitalized
        }
    }

    private static func styleLabel(_ style: String) -> String {
        switch style {
        case "simple": return "Simple, one concept at a time"
        case "intermediate": return "Balanced depth, some variations"
        case "advanced": return "Full analysis, all variations"
        default: return style.isEmpty ? "—" : style.capitalized
        }
    }

    private static func percent(_ value: Double) -> String {
        "\(Int((value * 100).rounded()))%"
    }
}
