import XCTest
@testable import Cereveon

private func decodePlan(_ json: String) -> TodayPlan {
    try! APIJSON.decode(TodayPlan.self, from: Data(json.utf8))
}

private func decodeDay(_ json: String) -> PlanDay {
    try! APIJSON.decode(PlanDay.self, from: Data(json.utf8))
}

/// Pure rendering helpers behind the iOS week-overview screen (phase 3b).
/// Mirrors the Android StudyPlanOverviewBottomSheetTest coverage.
@MainActor
final class StudyPlanOverviewViewTests: XCTestCase {

    func testFormatCategoryKnown() {
        XCTAssertEqual(StudyPlanOverviewView.formatCategory("tactical_vision"), "Tactics")
        XCTAssertEqual(StudyPlanOverviewView.formatCategory("endgame_technique"), "Endgames")
        XCTAssertEqual(StudyPlanOverviewView.formatCategory("opening_preparation"), "Openings")
        XCTAssertEqual(StudyPlanOverviewView.formatCategory("positional_play"), "Strategy")
    }

    func testFormatCategoryUnknownIsEmpty() {
        XCTAssertEqual(StudyPlanOverviewView.formatCategory(nil), "")
        XCTAssertEqual(StudyPlanOverviewView.formatCategory("generic"), "")
        XCTAssertEqual(StudyPlanOverviewView.formatCategory("nonsense"), "")
    }

    func testFormatFocusPrefersCategory() {
        let plan = decodePlan(#"{"plan_id":"p","anchor_category":"tactical_vision","theme":"king_safety"}"#)
        XCTAssertEqual(StudyPlanOverviewView.formatFocus(plan), "Tactics")
    }

    func testFormatFocusFallsBackToTheme() {
        let plan = decodePlan(#"{"plan_id":"p","theme":"king_safety"}"#)
        XCTAssertEqual(StudyPlanOverviewView.formatFocus(plan), "King safety")
    }

    func testFormatFocusNeutralDefault() {
        let plan = decodePlan(#"{"plan_id":"p","theme":"generic"}"#)
        XCTAssertEqual(StudyPlanOverviewView.formatFocus(plan), "This week")
    }

    func testDayLabel() {
        let original = decodeDay(#"{"day_offset":0,"source_type":"original"}"#)
        XCTAssertEqual(StudyPlanOverviewView.dayLabel(original), "Day 1 · Replay your mistake")
        let library = decodeDay(#"{"day_offset":3,"source_type":"library"}"#)
        XCTAssertEqual(StudyPlanOverviewView.dayLabel(library), "Day 2 · Practice")
    }

    func testStatusText() {
        XCTAssertEqual(
            StudyPlanOverviewView.statusText(decodeDay(#"{"day_offset":0,"completed":true}"#)), "Done")
        XCTAssertEqual(
            StudyPlanOverviewView.statusText(decodeDay(#"{"day_offset":3,"is_due":true}"#)), "Today")
        XCTAssertEqual(
            StudyPlanOverviewView.statusText(decodeDay(#"{"day_offset":7}"#)), "Locked")
        // completed wins over is_due
        XCTAssertEqual(
            StudyPlanOverviewView.statusText(decodeDay(#"{"day_offset":0,"completed":true,"is_due":true}"#)),
            "Done")
    }

    func testFormatProgress() {
        let fresh = [
            decodeDay(#"{"day_offset":0,"is_due":true}"#),
            decodeDay(#"{"day_offset":3}"#),
            decodeDay(#"{"day_offset":7}"#),
        ]
        XCTAssertEqual(StudyPlanOverviewView.formatProgress(fresh, 3), "Day 1 of 3")

        let mid = [
            decodeDay(#"{"day_offset":0,"completed":true}"#),
            decodeDay(#"{"day_offset":3,"is_due":true}"#),
            decodeDay(#"{"day_offset":7}"#),
        ]
        XCTAssertEqual(StudyPlanOverviewView.formatProgress(mid, 3), "Day 2 of 3")

        let done = [
            decodeDay(#"{"day_offset":0,"completed":true}"#),
            decodeDay(#"{"day_offset":3,"completed":true}"#),
            decodeDay(#"{"day_offset":7,"completed":true}"#),
        ]
        XCTAssertEqual(StudyPlanOverviewView.formatProgress(done, 3), "Week complete")
    }
}
