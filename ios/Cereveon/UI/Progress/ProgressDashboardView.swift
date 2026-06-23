import SwiftUI

/// Cereveon · Atrium · Progress dashboard (iOS port of the Android
/// `ProgressDashboardBottomSheet`, post-Elo-removal). Three sections: the
/// weakness profile (category bars), "how the coach sees you" (world-model
/// snapshot), and training focus (recommendations). The rating number is never
/// shown. Pushed from `SettingsView`; built with a token provider so it carries
/// the player's Bearer (and TLS pinning).
///
/// Named `ProgressDashboardView` (not `ProgressView`) to avoid shadowing
/// SwiftUI's built-in `ProgressView` spinner used below.
struct ProgressDashboardView: View {
    @StateObject private var vm: ProgressViewModel

    init(token: @escaping () -> String?) {
        _vm = StateObject(wrappedValue: ProgressViewModel(
            client: HTTPProgressClient(delegate: PinningURLSessionDelegate()),
            token: token
        ))
    }

    var body: some View {
        ZStack {
            AtriumBackground()

            switch vm.state {
            case .loading:
                ProgressView().tint(AtriumColors.accentCyan)
            case .error:
                message("Couldn't load your progress.", "Try again in a moment.")
            case .empty:
                message("No progress yet.", "Play a few games and your profile will appear here.")
            case let .loaded(weaknesses, worldModel, recommendations):
                loaded(weaknesses, worldModel, recommendations)
            }
        }
        .navigationTitle("Your progress")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await vm.load() }
    }

    // MARK: - Loaded

    private func loaded(_ weaknesses: [WeaknessEntry],
                        _ worldModel: [WorldModelRow],
                        _ recommendations: [RecommendationRow]) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AtriumSpacing.space32) {
                if !weaknesses.isEmpty {
                    section("Weakness profile") { weaknessChart(weaknesses) }
                }
                section("How the coach sees you") { worldModelRows(worldModel) }
                if !recommendations.isEmpty {
                    section("Training focus") { recommendationRows(recommendations) }
                }
            }
            .padding(AtriumSpacing.space24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func weaknessChart(_ entries: [WeaknessEntry]) -> some View {
        let maxValue = max(entries.map(\.value).max() ?? 1, 0.0001)
        return VStack(spacing: AtriumSpacing.space16) {
            ForEach(entries) { entry in
                VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
                    HStack {
                        Text(entry.label)
                            .atriumStyle(AtriumTypography.inline)
                            .foregroundStyle(AtriumColors.ink)
                        Spacer()
                        if !entry.priority.isEmpty {
                            Text(entry.priority.uppercased())
                                .atriumStyle(AtriumTypography.kicker)
                                .foregroundStyle(priorityColor(entry.priority))
                        }
                    }
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(AtriumColors.hairline)
                            Capsule().fill(priorityColor(entry.priority))
                                .frame(width: max(6, geo.size.width * CGFloat(entry.value / maxValue)))
                        }
                    }
                    .frame(height: 6)
                }
            }
        }
    }

    private func worldModelRows(_ rows: [WorldModelRow]) -> some View {
        VStack(spacing: 0) {
            ForEach(rows) { row in
                HStack(alignment: .firstTextBaseline) {
                    Text(row.label.uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(AtriumColors.dim)
                    Spacer(minLength: AtriumSpacing.space16)
                    Text(row.value)
                        .atriumStyle(AtriumTypography.inline)
                        .foregroundStyle(AtriumColors.ink)
                        .multilineTextAlignment(.trailing)
                }
                .padding(.vertical, AtriumSpacing.space8)
            }
        }
    }

    private func recommendationRows(_ recs: [RecommendationRow]) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
            ForEach(recs) { rec in
                VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
                    HStack(spacing: AtriumSpacing.space8) {
                        Text(rec.priority.uppercased())
                            .atriumStyle(AtriumTypography.kicker)
                            .foregroundStyle(priorityColor(rec.priority))
                        Text(rec.category)
                            .atriumStyle(AtriumTypography.body)
                            .foregroundStyle(AtriumColors.ink)
                    }
                    Text(rec.rationale)
                        .atriumStyle(AtriumTypography.inline)
                        .foregroundStyle(AtriumColors.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - Components

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space12) {
            Text(title.uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.muted)
            content()
        }
    }

    private func message(_ title: String, _ subtitle: String) -> some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text(title)
                .atriumStyle(AtriumTypography.display)
                .foregroundStyle(AtriumColors.ink)
            Text(subtitle)
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
                .multilineTextAlignment(.center)
        }
        .padding(AtriumSpacing.space32)
    }

    private func priorityColor(_ priority: String) -> Color {
        switch priority {
        case "high": return AtriumColors.accentAmber
        case "medium": return AtriumColors.accentAmberCc
        default: return AtriumColors.accentCyan
        }
    }
}
