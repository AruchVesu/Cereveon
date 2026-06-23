import SwiftUI

/// Cereveon · Atrium · Lessons — the next recommended study focus from
/// POST /curriculum/next. A recommendation card (topic / exercise / difficulty /
/// session length + training focus), NOT an on-board solver: the curriculum is a
/// recommender, not a puzzle source. Presented full-screen from the Home
/// "Lessons" row / tab.
struct LessonsView: View {
    @StateObject private var vm: LessonsViewModel
    @Environment(\.dismiss) private var dismiss
    private let auth: AuthViewModel
    @State private var showSession = false

    init(auth: AuthViewModel) {
        self.auth = auth
        _vm = StateObject(wrappedValue: LessonsViewModel(
            client: HTTPCurriculumClient(delegate: PinningURLSessionDelegate()),
            token: { auth.bearerToken }
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AtriumBackground()
                switch vm.state {
                case .loading:
                    ProgressView().tint(AtriumColors.accentCyan)
                case .error:
                    message("Couldn't load your lesson.", "Try again in a moment.")
                case let .loaded(plan):
                    content(plan)
                }
            }
            .navigationTitle("Lessons")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }.foregroundStyle(AtriumColors.accentCyan)
                }
            }
            .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .tint(AtriumColors.accentCyan)
        .task { await vm.load() }
        .sheet(isPresented: $showSession) {
            if case let .loaded(plan) = vm.state {
                LessonChatView(topic: plan.topic,
                               exerciseType: plan.exerciseType,
                               difficulty: plan.difficulty,
                               auth: auth)
            }
        }
    }

    private func content(_ plan: CurriculumNext) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: AtriumSpacing.space24) {
                focusCard(plan)
                if !plan.recommendations.isEmpty {
                    section("Training focus") {
                        VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
                            ForEach(plan.recommendations, id: \.category) { rec in
                                recommendationRow(rec)
                            }
                        }
                    }
                }
                AtriumPrimaryButton(title: "Start session") { showSession = true }
                AtriumSecondaryButton(title: "Get another") { Task { await vm.load() } }
            }
            .padding(AtriumSpacing.space24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func focusCard(_ plan: CurriculumNext) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            Text("Today's focus".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
            Text(LessonsViewModel.humanize(plan.topic))
                .atriumStyle(AtriumTypography.display)
                .foregroundStyle(AtriumColors.ink)
            HStack(spacing: AtriumSpacing.space16) {
                metaChip("Exercise", LessonsViewModel.humanize(plan.exerciseType))
                metaChip("Level", LessonsViewModel.humanize(plan.difficulty))
                if plan.sessionMinutes > 0 {
                    metaChip("Session", "~\(plan.sessionMinutes) min")
                }
            }
            .padding(.top, AtriumSpacing.space8)
        }
    }

    private func metaChip(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
            Text(label.uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.dim)
            Text(value)
                .atriumStyle(AtriumTypography.inline)
                .foregroundStyle(AtriumColors.ink)
        }
    }

    private func recommendationRow(_ rec: ProgressRecommendation) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
            HStack(spacing: AtriumSpacing.space8) {
                Text(rec.priority.uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(priorityColor(rec.priority))
                Text(LessonsViewModel.humanize(rec.category))
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.ink)
            }
            Text(rec.rationale)
                .atriumStyle(AtriumTypography.inline)
                .foregroundStyle(AtriumColors.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space12) {
            Text(title.uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.muted)
            content()
        }
    }

    private func priorityColor(_ priority: String) -> Color {
        switch priority {
        case "high": return AtriumColors.accentAmber
        case "medium": return AtriumColors.accentAmberCc
        default: return AtriumColors.accentCyan
        }
    }

    private func message(_ title: String, _ subtitle: String) -> some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text(title).atriumStyle(AtriumTypography.display).foregroundStyle(AtriumColors.ink)
            Text(subtitle).atriumStyle(AtriumTypography.bodyItalic).foregroundStyle(AtriumColors.muted)
                .multilineTextAlignment(.center)
        }
        .padding(AtriumSpacing.space32)
    }
}
