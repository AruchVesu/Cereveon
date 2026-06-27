import XCTest
@testable import Cereveon

private func decodePlan(_ json: String) -> TodayPlan {
    try! APIJSON.decode(TodayPlan.self, from: Data(json.utf8))
}

private final class FakeStudyPlanClient: StudyPlanClient {
    let result: APIResult<TodayPlan?>
    let completeResult: APIResult<TodayPlan>
    init(_ result: APIResult<TodayPlan?>, complete: APIResult<TodayPlan> = .timeout) {
        self.result = result
        self.completeResult = complete
    }
    func today(token: String) async -> APIResult<TodayPlan?> { result }
    func complete(planId: String, dayOffset: Int, token: String) async -> APIResult<TodayPlan> {
        completeResult
    }
}

private let planJSON = """
{"plan_id":"p1","theme":"forks","verdict":"You hung a knight to a fork.","total_days":3,
 "today_puzzle":{"day_offset":3,"fen":"r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3","expected_move_uci":"f1b5","source_type":"mistake_replay","due_at":"2026-06-23T00:00:00Z"}}
"""

/// Full overview shape: anchor_category + status + days[], all days solved.
private let fullPlanJSON = """
{"plan_id":"p1","theme":"king_safety","verdict":"",
 "anchor_category":"tactical_vision","status":"completed","total_days":3,
 "today_puzzle":null,
 "days":[
   {"day_offset":0,"due_at":"2026-06-20T00:00:00","completed":true,"is_due":false,"source_type":"original"},
   {"day_offset":3,"due_at":"2026-06-23T00:00:00","completed":true,"is_due":false,"source_type":"library"},
   {"day_offset":7,"due_at":"2026-06-27T00:00:00","completed":true,"is_due":false,"source_type":"library"}
 ]}
"""

@MainActor
final class StudyPlanTests: XCTestCase {

    override func setUp() { super.setUp(); URLProtocolStub.handler = nil; URLProtocolStub.lastRequest = nil }
    override func tearDown() { URLProtocolStub.handler = nil; URLProtocolStub.lastRequest = nil; super.tearDown() }

    private func stubConfig() -> URLSessionConfiguration {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [URLProtocolStub.self]
        return cfg
    }

    func testDecodePlanAndPuzzle() {
        let plan = decodePlan(planJSON)
        XCTAssertEqual(plan.theme, "forks")
        XCTAssertEqual(plan.totalDays, 3)
        XCTAssertTrue(plan.hasDuePuzzle)
        XCTAssertEqual(plan.todayPuzzle?.dayOffset, 3)
        XCTAssertEqual(plan.todayPuzzle?.dayNumber, 2, "offset 3 → Day 2")
        XCTAssertEqual(plan.todayPuzzle?.expectedMoveUci, "f1b5")
    }

    func testClientNullBodyIsNoCard() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data("null".utf8))
        }
        let client = HTTPStudyPlanClient(baseURL: "https://test.local", configuration: stubConfig())
        guard case let .success(plan) = await client.today(token: "tok") else { return XCTFail() }
        XCTAssertNil(plan, "a bare `null` body → no card")
        XCTAssertEqual(URLProtocolStub.lastRequest?.url?.path, "/coach/plan/today")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
    }

    func testClientDecodesPlanBody() async {
        let json = planJSON
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data(json.utf8))
        }
        let client = HTTPStudyPlanClient(baseURL: "https://test.local", configuration: stubConfig())
        guard case let .success(plan) = await client.today(token: "tok") else { return XCTFail() }
        XCTAssertEqual(plan?.theme, "forks")
        XCTAssertEqual(plan?.todayPuzzle?.expectedMoveUci, "f1b5")
    }

    func testViewModelLoadAndLoggedOut() async {
        let withPlan = TodaysDrillViewModel(client: FakeStudyPlanClient(.success(decodePlan(planJSON))))
        await withPlan.load(token: "t")
        XCTAssertNotNil(withPlan.puzzle)

        let loggedOut = TodaysDrillViewModel(client: FakeStudyPlanClient(.success(decodePlan(planJSON))))
        await loggedOut.load(token: nil)
        XCTAssertNil(loggedOut.plan, "no token → no fetch, no card")
    }

    func testDecodeOverviewFields() {
        let plan = decodePlan(fullPlanJSON)
        XCTAssertEqual(plan.anchorCategory, "tactical_vision")
        XCTAssertEqual(plan.status, "completed")
        XCTAssertEqual(plan.days.count, 3)
        XCTAssertTrue(plan.days.allSatisfy { $0.completed })
        XCTAssertEqual(plan.days.first?.sourceType, "original")
        XCTAssertEqual(plan.days.first?.dayNumber, 1)
        XCTAssertNil(plan.todayPuzzle)
    }

    func testLegacyPlanUsesDefaults() {
        // The original planJSON predates anchor_category / status / days.
        let plan = decodePlan(planJSON)
        XCTAssertNil(plan.anchorCategory, "missing anchor_category → nil")
        XCTAssertEqual(plan.status, "active", "missing status → default active")
        XCTAssertTrue(plan.days.isEmpty, "missing days → empty")
    }

    func testCompletePostsAndDecodes() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
             Data(fullPlanJSON.utf8))
        }
        let client = HTTPStudyPlanClient(baseURL: "https://test.local", configuration: stubConfig())
        guard case let .success(plan) =
                await client.complete(planId: "p1", dayOffset: 7, token: "tok")
        else { return XCTFail() }
        XCTAssertEqual(plan.status, "completed")
        XCTAssertEqual(plan.anchorCategory, "tactical_vision")
        XCTAssertEqual(URLProtocolStub.lastRequest?.url?.path, "/coach/plan/puzzle/complete")
        XCTAssertEqual(URLProtocolStub.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(
            URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"),
            "Bearer tok"
        )
    }
}
