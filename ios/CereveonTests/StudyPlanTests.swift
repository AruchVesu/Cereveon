import XCTest
@testable import Cereveon

private func decodePlan(_ json: String) -> TodayPlan {
    try! APIJSON.decode(TodayPlan.self, from: Data(json.utf8))
}

private final class FakeStudyPlanClient: StudyPlanClient {
    let result: APIResult<TodayPlan?>
    init(_ result: APIResult<TodayPlan?>) { self.result = result }
    func today(token: String) async -> APIResult<TodayPlan?> { result }
}

private let planJSON = """
{"plan_id":"p1","theme":"forks","verdict":"You hung a knight to a fork.","total_days":3,
 "today_puzzle":{"day_offset":3,"fen":"r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3","expected_move_uci":"f1b5","source_type":"mistake_replay","due_at":"2026-06-23T00:00:00Z"}}
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
}
