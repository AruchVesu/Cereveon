import XCTest
@testable import Cereveon

private func decodeRepertoire(_ json: String) -> RepertoireResponse {
    try! APIJSON.decode(RepertoireResponse.self, from: Data(json.utf8))
}

private final class FakeRepertoireClient: RepertoireClient {
    var getResult: APIResult<RepertoireResponse> = .httpError(500)
    var mutationResult: APIResult<RepertoireResponse> = .httpError(500)
    private(set) var calls: [String] = []
    private(set) var lastAdd: (eco: String, name: String, line: String)?
    private(set) var lastDrill: (eco: String, outcome: Double)?

    func getRepertoire(token: String) async -> APIResult<RepertoireResponse> {
        calls.append("get"); return getResult
    }
    func addOpening(eco: String, name: String, line: String, token: String) async -> APIResult<RepertoireResponse> {
        lastAdd = (eco, name, line); calls.append("add"); return mutationResult
    }
    func deleteOpening(eco: String, token: String) async -> APIResult<RepertoireResponse> {
        calls.append("delete:\(eco)"); return mutationResult
    }
    func setActive(eco: String, token: String) async -> APIResult<RepertoireResponse> {
        calls.append("active:\(eco)"); return mutationResult
    }
    func drillResult(eco: String, outcome: Double, token: String) async -> APIResult<RepertoireResponse> {
        lastDrill = (eco, outcome); calls.append("drill"); return mutationResult
    }
}

@MainActor
final class OpeningsTests: XCTestCase {

    private let twoLines = """
    {"openings":[
      {"eco":"B22","name":"Sicilian Alapin","line":"1.e4 c5 2.c3","mastery":0.55,"is_active":false,"ordinal":1},
      {"eco":"C84","name":"Ruy Lopez","line":"1.e4 e5 2.Nf3 Nc6 3.Bb5 a6","mastery":0.78,"is_active":true,"ordinal":0}
    ]}
    """

    private func makeVM(_ fake: FakeRepertoireClient, token: String? = "t") -> OpeningsViewModel {
        OpeningsViewModel(client: fake, token: { token })
    }

    func testLoadSortsByOrdinalAndPicksActive() async {
        let fake = FakeRepertoireClient(); fake.getResult = .success(decodeRepertoire(twoLines))
        let vm = makeVM(fake)
        await vm.load()
        XCTAssertEqual(vm.openings.map(\.eco), ["C84", "B22"], "sorted by ordinal ascending")
        XCTAssertEqual(vm.activeOpening?.eco, "C84")
    }

    func testLoadErrorAndLoggedOut() async {
        let errored = makeVM({ let f = FakeRepertoireClient(); f.getResult = .httpError(500); return f }())
        await errored.load()
        XCTAssertEqual(errored.state, .error)

        let loggedOut = makeVM(FakeRepertoireClient(), token: nil)
        await loggedOut.load()
        XCTAssertEqual(loggedOut.state, .error)
    }

    func testSetActiveAppliesServerResponse() async {
        let fake = FakeRepertoireClient(); fake.getResult = .success(decodeRepertoire(twoLines))
        let vm = makeVM(fake)
        await vm.load()
        // Server now reports B22 as active.
        let flipped = """
        {"openings":[
          {"eco":"C84","name":"Ruy Lopez","line":"1.e4 e5","mastery":0.78,"is_active":false,"ordinal":0},
          {"eco":"B22","name":"Sicilian Alapin","line":"1.e4 c5","mastery":0.55,"is_active":true,"ordinal":1}
        ]}
        """
        fake.mutationResult = .success(decodeRepertoire(flipped))
        await vm.setActive("B22")
        XCTAssertTrue(fake.calls.contains("active:B22"))
        XCTAssertEqual(vm.activeOpening?.eco, "B22")
    }

    func testAddValidationBlocksEmptyFields() async {
        let fake = FakeRepertoireClient(); fake.getResult = .success(decodeRepertoire(twoLines))
        let vm = makeVM(fake)
        await vm.load()
        await vm.add(eco: "  ", name: "X", line: "1.e4")
        XCTAssertFalse(fake.calls.contains("add"), "blank ECO → no network call")
        XCTAssertNotNil(vm.banner)
    }

    func testAddTrimsAndUppercasesEco() async {
        let fake = FakeRepertoireClient()
        fake.getResult = .success(decodeRepertoire(twoLines))
        fake.mutationResult = .success(decodeRepertoire(twoLines))
        let vm = makeVM(fake)
        await vm.load()
        await vm.add(eco: "  c99 ", name: "  My Line ", line: " 1.e4 e5 ")
        XCTAssertEqual(fake.lastAdd?.eco, "C99")
        XCTAssertEqual(fake.lastAdd?.name, "My Line")
        XCTAssertEqual(fake.lastAdd?.line, "1.e4 e5")
    }

    func testRecordDrillForwardsOutcome() async {
        let fake = FakeRepertoireClient()
        fake.getResult = .success(decodeRepertoire(twoLines))
        fake.mutationResult = .success(decodeRepertoire(twoLines))
        let vm = makeVM(fake)
        await vm.load()
        await vm.recordDrill("C84", outcome: 0.6)
        XCTAssertEqual(fake.lastDrill?.eco, "C84")
        XCTAssertEqual(fake.lastDrill?.outcome ?? 0, 0.6, accuracy: 0.0001)
    }

    func testMutationErrorShowsBanner() async {
        let fake = FakeRepertoireClient()
        fake.getResult = .success(decodeRepertoire(twoLines))
        fake.mutationResult = .httpError(400)
        let vm = makeVM(fake)
        await vm.load()
        await vm.delete("B22")
        XCTAssertEqual(vm.banner, "Invalid opening — check the ECO format.")
    }

    func testAvgDepth() {
        let json = """
        {"openings":[
          {"eco":"A","name":"a","line":"1.e4 e5","mastery":0,"is_active":false,"ordinal":0},
          {"eco":"B","name":"b","line":"1.e4 e5 2.Nf3 Nc6","mastery":0,"is_active":false,"ordinal":1}
        ]}
        """
        let openings = decodeRepertoire(json).openings
        XCTAssertEqual(OpeningsViewModel.avgDepth(openings), 3)   // (2 + 4) / 2
        XCTAssertEqual(OpeningsViewModel.avgDepth([]), 0)
    }

    func testFormatMastery() {
        XCTAssertEqual(OpeningsView.formatMastery(0.78), "78%")
        XCTAssertEqual(OpeningsView.formatMastery(0), "0%")
        XCTAssertEqual(OpeningsView.formatMastery(1.5), "100%")
    }
}
