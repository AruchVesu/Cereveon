import Foundation
import Combine

/// Loads the active study plan's due puzzle (GET /coach/plan/today) for the Home
/// "Today's drill" card. Silent: any failure / no-plan / no-due-puzzle simply
/// leaves `plan` nil and the card hidden (matches Android's phase-4 behaviour).
@MainActor
final class TodaysDrillViewModel: ObservableObject {
    @Published private(set) var plan: TodayPlan?

    private let client: StudyPlanClient

    init(client: StudyPlanClient = HTTPStudyPlanClient(delegate: PinningURLSessionDelegate())) {
        self.client = client
    }

    var puzzle: TodayPuzzle? { plan?.todayPuzzle }

    func load(token: String?) async {
        guard let token else { plan = nil; return }
        if case let .success(loaded) = await client.today(token: token) {
            plan = loaded
        }
    }

    /// Apply an authoritative plan pushed by the week overview after it
    /// advances a solved day, so the Home card reflects the new state without
    /// a second /coach/plan/today round-trip.
    func apply(_ plan: TodayPlan) { self.plan = plan }
}
