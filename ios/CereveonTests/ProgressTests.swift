import XCTest
@testable import Cereveon

private func decodeProgress(_ json: String) -> PlayerProgressResponse {
    try! JSONDecoder().decode(PlayerProgressResponse.self, from: Data(json.utf8))
}

private final class FakeProgressClient: ProgressClient {
    let result: APIResult<PlayerProgressResponse>
    init(_ result: APIResult<PlayerProgressResponse>) { self.result = result }
    func progress(token: String) async -> APIResult<PlayerProgressResponse> { result }
}

private let sampleJSON = """
{
  "current": {
    "rating": 1500.0, "confidence": 0.6,
    "skill_vector": {"tactical_vision": 0.3},
    "tier": "intermediate", "teaching_style": "intermediate",
    "opponent_elo": 1460, "explanation_depth": 0.5, "concept_complexity": 0.42
  },
  "history": [],
  "analysis": {
    "dominant_category": "tactical_vision", "games_analyzed": 4,
    "category_scores": {"tactical_vision": 0.8, "endgame_technique": 0.4},
    "phase_rates": {},
    "recommendations": [
      {"category": "tactical_vision", "priority": "high", "rationale": "Missed tactics."}
    ]
  }
}
"""

@MainActor
final class ProgressTests: XCTestCase {

    override func setUp() { super.setUp(); URLProtocolStub.handler = nil; URLProtocolStub.lastRequest = nil }
    override func tearDown() { URLProtocolStub.handler = nil; URLProtocolStub.lastRequest = nil; super.tearDown() }

    // MARK: - Decode (the snake_case dict-key fidelity is the critical bit)

    func testDecodeKeepsDictKeysLiteral() {
        let data = decodeProgress(sampleJSON)
        XCTAssertEqual(data.current.tier, "intermediate")
        XCTAssertEqual(data.current.explanationDepth, 0.5, accuracy: 0.0001)
        XCTAssertEqual(data.current.conceptComplexity, 0.42, accuracy: 0.0001)
        // A plain decoder must keep "tactical_vision" literal (convertFromSnakeCase
        // would rewrite the dict key to "tacticalVision").
        XCTAssertEqual(data.analysis.categoryScores["tactical_vision"], 0.8)
        XCTAssertEqual(data.current.skillVector["tactical_vision"], 0.3)
        XCTAssertEqual(data.analysis.recommendations.first?.priority, "high")
    }

    // MARK: - Pure mapping

    func testWeaknessEntriesSortedLabeledAndPrioritised() {
        let entries = ProgressViewModel.weaknessEntries(from: decodeProgress(sampleJSON))
        XCTAssertEqual(entries.map(\.label), ["Tactics", "Endgame"], "sorted desc by score, mapped labels")
        XCTAssertEqual(entries.first?.priority, "high")
        XCTAssertEqual(entries.first?.value ?? 0, 0.8, accuracy: 0.0001)
    }

    func testWorldModelAndRecommendationRows() {
        let data = decodeProgress(sampleJSON)
        let worldModel = ProgressViewModel.worldModelRows(from: data.current)
        XCTAssertEqual(worldModel.map(\.label), ["Tier", "Coach style", "Depth", "Complexity"])
        XCTAssertEqual(worldModel.first { $0.label == "Depth" }?.value, "50%")
        XCTAssertEqual(worldModel.first { $0.label == "Tier" }?.value, "Intermediate — building concepts")

        let recs = ProgressViewModel.recommendationRows(from: data.analysis)
        XCTAssertEqual(recs.first?.category, "Tactics")
        XCTAssertEqual(recs.first?.priority, "high")
        XCTAssertEqual(recs.first?.rationale, "Missed tactics.")
    }

    // MARK: - View-model load states

    func testLoadSuccessSetsLoaded() async {
        let vm = ProgressViewModel(client: FakeProgressClient(.success(decodeProgress(sampleJSON))), token: { "t" })
        await vm.load()
        guard case let .loaded(weaknesses, worldModel, recommendations) = vm.state else {
            return XCTFail("expected loaded: \(vm.state)")
        }
        XCTAssertEqual(weaknesses.count, 2)
        XCTAssertEqual(worldModel.count, 4)
        XCTAssertEqual(recommendations.count, 1)
    }

    func testLoadEmptyWhenNoWeaknessesOrRecs() async {
        let emptyJSON = """
        {"current":{"tier":"intermediate","teaching_style":"","explanation_depth":0,"concept_complexity":0,"skill_vector":{}},
         "analysis":{"category_scores":{},"recommendations":[]}}
        """
        let vm = ProgressViewModel(client: FakeProgressClient(.success(decodeProgress(emptyJSON))), token: { "t" })
        await vm.load()
        XCTAssertEqual(vm.state, .empty)
    }

    func testLoadErrorOnHttpError() async {
        let vm = ProgressViewModel(client: FakeProgressClient(.httpError(500)), token: { "t" })
        await vm.load()
        XCTAssertEqual(vm.state, .error)
    }

    func testLoadErrorWhenLoggedOut() async {
        let vm = ProgressViewModel(client: FakeProgressClient(.httpError(500)), token: { nil })
        await vm.load()
        XCTAssertEqual(vm.state, .error)
    }

    // MARK: - Client over the stub (path + auth + plain decoder end-to-end)

    func testClientSendsAuthAndDecodesOverStub() async {
        URLProtocolStub.handler = { req in
            (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
             Data(sampleJSON.utf8))
        }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [URLProtocolStub.self]
        let client = HTTPProgressClient(baseURL: "https://test.local", configuration: cfg)

        guard case let .success(data) = await client.progress(token: "tok") else {
            return XCTFail("expected success")
        }
        XCTAssertEqual(data.analysis.categoryScores["tactical_vision"], 0.8)
        XCTAssertEqual(URLProtocolStub.lastRequest?.url?.path, "/player/progress")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
        XCTAssertEqual(URLProtocolStub.lastRequest?.value(forHTTPHeaderField: "X-Api-Key"), AppConfig.apiKey)
    }
}
