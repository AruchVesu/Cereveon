import SwiftUI

/// Cereveon · Atrium · Study-plan week overview (iOS phase 3b).
///
/// Renders the whole weekly curriculum at a glance — the aggregate
/// dominant-weakness focus (`anchorCategory`), the LLM coach verdict,
/// "Day N of 3" progress, and the three spaced-repetition days each
/// marked **Today / Done / Locked** — with a CTA that launches the
/// existing drill (`MistakeReplayView`) for the currently-due puzzle.
///
/// On a verified-correct solve the drill fires `onSolved`, which advances
/// the plan (POST /coach/plan/puzzle/complete) and updates the in-place
/// `plan` state so reopening / dismissing the drill shows fresh progress.
/// Presented from `HomeView` inside a `NavigationStack` (which supplies
/// the Close toolbar item).
struct StudyPlanOverviewView: View {
    @State private var plan: TodayPlan
    @State private var showDrill = false
    private let token: () -> String?
    private let client: StudyPlanClient

    init(plan: TodayPlan,
         token: @escaping () -> String?,
         client: StudyPlanClient = HTTPStudyPlanClient(delegate: PinningURLSessionDelegate())) {
        _plan = State(initialValue: plan)
        self.token = token
        self.client = client
    }

    var body: some View {
        ZStack {
            AtriumBackground()
            ScrollView {
                VStack(alignment: .leading, spacing: AtriumSpacing.space16) {
                    focusHeader

                    if !plan.verdict.isEmpty {
                        Text(plan.verdict)
                            .atriumStyle(AtriumTypography.body)
                            .foregroundStyle(AtriumColors.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Text(Self.formatProgress(plan.days, plan.totalDays).uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(AtriumColors.dim)

                    daysList

                    if plan.todayPuzzle != nil {
                        AtriumPrimaryButton(title: ctaTitle) { showDrill = true }
                            .padding(.top, AtriumSpacing.space8)
                    }
                }
                .padding(AtriumSpacing.space24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .navigationTitle("This week")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .fullScreenCover(isPresented: $showDrill) {
            if let fen = plan.todayPuzzle?.fen, !fen.isEmpty {
                NavigationStack {
                    MistakeReplayView(positions: [fen], token: token, onSolved: { advanceCurrentDay() })
                        .toolbar {
                            ToolbarItem(placement: .navigationBarLeading) {
                                Button("Close") { showDrill = false }
                                    .foregroundStyle(AtriumColors.muted)
                            }
                        }
                }
                .tint(AtriumColors.accentCyan)
            }
        }
    }

    private var focusHeader: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            Text("This week".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
            Text(Self.formatFocus(plan))
                .atriumStyle(AtriumTypography.display)
                .foregroundStyle(AtriumColors.ink)
        }
    }

    private var daysList: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space12) {
            ForEach(plan.days, id: \.dayOffset) { day in
                HStack(alignment: .firstTextBaseline) {
                    Text(Self.dayLabel(day))
                        .atriumStyle(AtriumTypography.body)
                        .foregroundStyle(AtriumColors.ink)
                    Spacer(minLength: AtriumSpacing.space8)
                    Text(Self.statusText(day).uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(Self.statusColor(day))
                }
            }
        }
    }

    private var ctaTitle: String {
        guard let puzzle = plan.todayPuzzle else { return "Start" }
        return "Start day \(puzzle.dayNumber)"
    }

    /// Advance the plan after a verified-correct solve.  Best-effort: if
    /// the call fails the day resurfaces on the next /coach/plan/today
    /// fetch (the endpoint is idempotent), so we don't surface an error.
    private func advanceCurrentDay() {
        guard let day = plan.todayPuzzle?.dayOffset, let bearer = token(), !plan.planId.isEmpty
        else { return }
        Task {
            if case let .success(updated) =
                await client.complete(planId: plan.planId, dayOffset: day, token: bearer) {
                plan = updated
            }
        }
    }

    // MARK: - Pure rendering helpers (unit-tested)

    /// Map an aggregate `anchorCategory` (one of the four MistakeCategory
    /// values) to a friendly focus noun.  Returns "" for nil / generic /
    /// unknown so the caller can fall back to the day-0 theme.
    static func formatCategory(_ category: String?) -> String {
        switch category?.trimmingCharacters(in: .whitespaces).lowercased() {
        case "tactical_vision": return "Tactics"
        case "endgame_technique": return "Endgames"
        case "opening_preparation": return "Openings"
        case "positional_play": return "Strategy"
        default: return ""
        }
    }

    /// The big focus title — the aggregate weakness, else the day-0
    /// theme, else a neutral default, so it's never blank.
    static func formatFocus(_ plan: TodayPlan) -> String {
        let byCategory = formatCategory(plan.anchorCategory)
        if !byCategory.isEmpty { return byCategory }
        let theme = plan.theme.trimmingCharacters(in: .whitespaces).lowercased()
        if !theme.isEmpty, theme != "generic" {
            return theme.split(separator: "_")
                .map { $0.prefix(1).uppercased() + $0.dropFirst() }
                .joined(separator: " ")
        }
        return "This week"
    }

    /// Row label: "Day N · Replay your mistake" (day-0 original) or
    /// "Day N · Practice" (library days).
    static func dayLabel(_ day: PlanDay) -> String {
        let kind = day.sourceType.trimmingCharacters(in: .whitespaces).lowercased() == "original"
            ? "Replay your mistake" : "Practice"
        return "Day \(day.dayNumber) · \(kind)"
    }

    /// Status word for one day: Done / Today / Locked.
    static func statusText(_ day: PlanDay) -> String {
        if day.completed { return "Done" }
        if day.isDue { return "Today" }
        return "Locked"
    }

    /// Status colour: cyan = today (actionable), muted = done, dim = locked.
    static func statusColor(_ day: PlanDay) -> Color {
        if day.completed { return AtriumColors.muted }
        if day.isDue { return AtriumColors.accentCyan }
        return AtriumColors.dim
    }

    /// "Day N of M", or "Week complete" once every day is solved.
    static func formatProgress(_ days: [PlanDay], _ totalDays: Int) -> String {
        let completed = days.filter { $0.completed }.count
        if totalDays > 0, completed >= totalDays { return "Week complete" }
        return "Day \(completed + 1) of \(totalDays)"
    }
}
