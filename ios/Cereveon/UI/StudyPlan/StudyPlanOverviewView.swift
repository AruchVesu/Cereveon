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
    @State private var activeDrill: DrillPosition?
    private let token: () -> String?
    private let client: StudyPlanClient
    /// Pushes the freshly-advanced plan back to Home's drill view-model so the
    /// "Today's drill" card reflects the solve immediately — without a second
    /// /coach/plan/today round-trip, and with no race between that re-poll and
    /// the in-flight advance.  `@MainActor` because it mutates the view-model.
    private let onAdvance: @MainActor (TodayPlan) -> Void

    init(plan: TodayPlan,
         token: @escaping () -> String?,
         client: StudyPlanClient = HTTPStudyPlanClient(delegate: PinningURLSessionDelegate()),
         onAdvance: @escaping @MainActor (TodayPlan) -> Void = { _ in }) {
        _plan = State(initialValue: plan)
        self.token = token
        self.client = client
        self.onAdvance = onAdvance
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

                    if let puzzle = plan.todayPuzzle {
                        AtriumPrimaryButton(title: Self.ctaTitle(puzzle.dayNumber)) { startDrill() }
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
        .fullScreenCover(item: $activeDrill) { drill in
            NavigationStack {
                MistakeReplayView(positions: [drill.fen], token: token, onSolved: { advanceCurrentDay() })
                    .toolbar {
                        ToolbarItem(placement: .navigationBarLeading) {
                            Button("Close") { activeDrill = nil }
                                .foregroundStyle(AtriumColors.muted)
                        }
                    }
            }
            .tint(AtriumColors.accentCyan)
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

    /// Present the drill for the currently-due puzzle by capturing its FEN
    /// as a stable item.  Presenting by *item* (not a `Bool` + a live
    /// `plan.todayPuzzle` read) keeps the drill alive when `advanceCurrentDay()`
    /// later nils out `todayPuzzle` mid-solve.  A missing/empty FEN is a no-op
    /// rather than a blank cover.
    private func startDrill() {
        guard let fen = plan.todayPuzzle?.fen, !fen.isEmpty else { return }
        activeDrill = DrillPosition(fen: fen)
    }

    /// Advance the plan after a verified-correct solve.  Best-effort: if
    /// the call fails the day resurfaces on the next /coach/plan/today
    /// fetch (the endpoint is idempotent), so we don't surface an error.
    /// On success it also pushes the refreshed plan to Home via `onAdvance`,
    /// so the card updates without a re-poll that could race this advance.
    private func advanceCurrentDay() {
        guard let day = plan.todayPuzzle?.dayOffset, let bearer = token(), !plan.planId.isEmpty
        else { return }
        Task {
            if case let .success(updated) =
                await client.complete(planId: plan.planId, dayOffset: day, token: bearer) {
                plan = updated
                await onAdvance(updated)
            }
        }
    }

    // MARK: - Pure rendering helpers (unit-tested)

    /// Map an aggregate `anchorCategory` (one of the four MistakeCategory
    /// values) to a friendly focus noun.  Returns "" for nil / generic /
    /// unknown so the caller can fall back to the day-0 theme.
    static func formatCategory(_ category: String?) -> String {
        switch category?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
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
        let theme = plan.theme.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !theme.isEmpty, theme != "generic" {
            // Sentence-case (first word only), matching Android's
            // prettyTheme: "king_safety" → "King safety".
            let spaced = theme.split(separator: "_").joined(separator: " ")
            let pretty = spaced.prefix(1).uppercased() + spaced.dropFirst()
            // Enforce the never-blank contract: a degenerate all-underscore
            // theme collapses to "" after the split, so fall through to the
            // neutral default rather than render an empty title.
            if !pretty.isEmpty { return pretty }
        }
        return "This week"
    }

    /// Row label: "Day N · Replay your mistake" (day-0 original) or
    /// "Day N · Practice" (library days).
    static func dayLabel(_ day: PlanDay) -> String {
        let kind = day.sourceType.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "original"
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

    /// Primary CTA label for the currently-due day.  Mirrors Android's
    /// formatCtaLabel ("Start day 2").
    static func ctaTitle(_ dayNumber: Int) -> String { "Start day \(dayNumber)" }
}

/// Identifiable FEN snapshot that drives the drill cover.  The cover is
/// presented by *item* rather than a `Bool` + a live `plan.todayPuzzle` read,
/// so that `advanceCurrentDay()` mutating `plan` to a nil `todayPuzzle`
/// mid-solve cannot collapse the cover's content and strand the user on a
/// blank, undismissable full-screen cover.
private struct DrillPosition: Identifiable {
    let id = UUID()
    let fen: String
}
