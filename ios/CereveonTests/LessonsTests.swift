import XCTest
@testable import Cereveon

private func decodeCurriculum(_ json: String) -> CurriculumNext {
    try! APIJSON.decode(CurriculumNext.self, from: Data(json.utf8))
}

private final class FakeCurriculumClient: CurriculumClient {
    let result: APIResult<CurriculumNext>
    init(_ result: APIResult<CurriculumNext>) { self.result = result }
    func next(token: String) async -> APIResult<CurriculumNext> { result }
}

@MainActor
final class LessonsTests: XCTestCase {

    private let sampleJSON = """
    {
      "topic": "tactical_vision",
      "difficulty": "intermediate",
      "exercise_type": "find_the_fork",
      "payload": {"session_minutes": 15, "focus": "tactical_vision", "difficulty": "intermediate", "exercise": "find_the_fork"},
      "recommendations": [{"category": "tactical_vision", "priority": "high", "rationale": "Work on tactics."}],
      "dominant_category": "tactical_vision"
    }
    """

    func testDecodePullsSessionFromPayload() {
        let plan = decodeCurriculum(sampleJSON)
        XCTAssertEqual(plan.topic, "tactical_vision")
        XCTAssertEqual(plan.exerciseType, "find_the_fork")
        XCTAssertEqual(plan.difficulty, "intermediate")
        XCTAssertEqual(plan.sessionMinutes, 15, "session_minutes is nested in payload")
        XCTAssertEqual(plan.recommendations.count, 1)
        XCTAssertEqual(plan.recommendations.first?.priority, "high")
    }

    func testDecodeDifficultyAsNumber() {
        let plan = decodeCurriculum(#"{"topic":"x","difficulty":3,"exercise_type":"y","payload":{},"recommendations":[]}"#)
        XCTAssertEqual(plan.difficulty, "3")
        XCTAssertEqual(plan.sessionMinutes, 0, "missing session → 0")
    }

    func testLoadSuccess() async {
        let vm = LessonsViewModel(client: FakeCurriculumClient(.success(decodeCurriculum(sampleJSON))), token: { "t" })
        await vm.load()
        guard case let .loaded(plan) = vm.state else { return XCTFail("expected loaded: \(vm.state)") }
        XCTAssertEqual(plan.topic, "tactical_vision")
        XCTAssertEqual(plan.recommendations.count, 1)
    }

    func testLoadErrorAndLoggedOut() async {
        let errored = LessonsViewModel(client: FakeCurriculumClient(.httpError(500)), token: { "t" })
        await errored.load()
        XCTAssertEqual(errored.state, .error)

        let loggedOut = LessonsViewModel(client: FakeCurriculumClient(.httpError(500)), token: { nil })
        await loggedOut.load()
        XCTAssertEqual(loggedOut.state, .error)
    }

    func testHumanize() {
        XCTAssertEqual(LessonsViewModel.humanize("tactical_vision"), "Tactical Vision")
        XCTAssertEqual(LessonsViewModel.humanize("find_the_fork"), "Find The Fork")
        XCTAssertEqual(LessonsViewModel.humanize(""), "—")
    }

    func testLessonChatSeedPrompt() {
        let prompt = LessonChatSeed.prompt(topic: "endgame_technique", exerciseType: "drill", difficulty: "Medium")
        XCTAssertEqual(prompt,
            "I want to train on endgame technique (Drill, medium difficulty). Please guide me through this training session.")
    }

    func testLessonChatSeedPromptEmptyType() {
        let prompt = LessonChatSeed.prompt(topic: "tactics", exerciseType: "", difficulty: "easy")
        XCTAssertTrue(prompt.contains("train on tactics"), prompt)
        XCTAssertTrue(prompt.contains("easy difficulty"), prompt)
    }
}
