import XCTest
@testable import Cereveon

private func statusDTO(linked: Bool, handle: String = "magnus", count: Int = 0) -> LichessStatus {
    let json = linked
        ? #"{"linked":true,"external_username":"\#(handle)","imported_game_count":\#(count)}"#
        : #"{"linked":false}"#
    return try! APIJSON.decode(LichessStatus.self, from: Data(json.utf8))
}

private func jobDTO(_ status: String, inserted: Int = 0, target: Int = 50, id: String = "j1") -> LichessImportJob {
    let json = #"{"job_id":"\#(id)","status":"\#(status)","inserted":\#(inserted),"target_max_games":\#(target)}"#
    return try! APIJSON.decode(LichessImportJob.self, from: Data(json.utf8))
}

private final class FakeLichessClient: LichessClient {
    var statusResults: [APIResult<LichessStatus>] = []
    var linkResult: APIResult<Void> = .success(())
    var unlinkResult: APIResult<Void> = .success(())
    var importStartResult: APIResult<LichessImportJob> = .httpError(500)
    var importJobResults: [APIResult<LichessImportJob>] = []
    private(set) var lastLinkUsername: String?
    private(set) var importStartCount = 0

    func status(token: String) async -> APIResult<LichessStatus> {
        if statusResults.isEmpty { return .httpError(500) }
        if statusResults.count == 1 { return statusResults[0] }
        return statusResults.removeFirst()
    }
    func link(username: String, token: String) async -> APIResult<Void> {
        lastLinkUsername = username
        return linkResult
    }
    func unlink(token: String) async -> APIResult<Void> { unlinkResult }
    func importGames(maxGames: Int, token: String) async -> APIResult<LichessImportJob> {
        importStartCount += 1
        return importStartResult
    }
    func importJob(jobId: String, token: String) async -> APIResult<LichessImportJob> {
        if importJobResults.isEmpty { return .httpError(500) }
        if importJobResults.count == 1 { return importJobResults[0] }
        return importJobResults.removeFirst()
    }
}

@MainActor
final class LichessTests: XCTestCase {

    private func makeVM(_ fake: FakeLichessClient, token: String? = "t") -> LichessConnectViewModel {
        LichessConnectViewModel(client: fake, token: { token }, pollIntervalNanos: 1_000, maxPolls: 10)
    }

    func testLoadNotLinked() async {
        let fake = FakeLichessClient(); fake.statusResults = [.success(statusDTO(linked: false))]
        let vm = makeVM(fake)
        await vm.load()
        XCTAssertEqual(vm.phase, .notLinked)
    }

    func testLoadLinked() async {
        let fake = FakeLichessClient(); fake.statusResults = [.success(statusDTO(linked: true, handle: "magnus", count: 12))]
        let vm = makeVM(fake)
        await vm.load()
        XCTAssertEqual(vm.phase, .linked(handle: "magnus", gameCount: 12))
    }

    func testLoadErrorAndLoggedOut() async {
        let errored = makeVM({ let f = FakeLichessClient(); f.statusResults = [.httpError(500)]; return f }())
        await errored.load()
        XCTAssertEqual(errored.phase, .error("Couldn't reach Lichess. Try again."))

        let loggedOut = makeVM(FakeLichessClient(), token: nil)
        await loggedOut.load()
        XCTAssertEqual(loggedOut.phase, .error("You're signed out."))
    }

    func testLinkSuccessReloadsStatus() async {
        let fake = FakeLichessClient()
        fake.statusResults = [.success(statusDTO(linked: false)), .success(statusDTO(linked: true, handle: "magnus", count: 0))]
        fake.linkResult = .success(())
        let vm = makeVM(fake)
        await vm.load()
        vm.usernameDraft = "  magnus  "
        await vm.link()
        XCTAssertEqual(fake.lastLinkUsername, "magnus", "trimmed username")
        XCTAssertEqual(vm.phase, .linked(handle: "magnus", gameCount: 0))
        XCTAssertEqual(vm.usernameDraft, "")
    }

    func testLinkFailureShowsBannerAndStaysNotLinked() async {
        let fake = FakeLichessClient()
        fake.statusResults = [.success(statusDTO(linked: false))]
        fake.linkResult = .httpError(404)
        let vm = makeVM(fake)
        await vm.load()
        vm.usernameDraft = "ghost"
        await vm.link()
        XCTAssertEqual(vm.phase, .notLinked)
        XCTAssertEqual(vm.banner, "Couldn't find that Lichess account.")
    }

    func testImportPollsUntilSucceeded() async {
        let fake = FakeLichessClient()
        fake.statusResults = [.success(statusDTO(linked: true, handle: "magnus", count: 0)),
                              .success(statusDTO(linked: true, handle: "magnus", count: 50))]
        fake.importStartResult = .success(jobDTO("running", inserted: 0))
        fake.importJobResults = [.success(jobDTO("running", inserted: 25)),
                                 .success(jobDTO("succeeded", inserted: 50))]
        let vm = makeVM(fake)
        await vm.load()
        vm.startImport()
        await vm.awaitImportCompletion()
        XCTAssertEqual(fake.importStartCount, 1)
        XCTAssertEqual(vm.phase, .linked(handle: "magnus", gameCount: 50))
        XCTAssertNil(vm.banner)
    }

    func testImportImmediateTerminal() async {
        let fake = FakeLichessClient()
        fake.statusResults = [.success(statusDTO(linked: true, count: 0)),
                              .success(statusDTO(linked: true, count: 7))]
        fake.importStartResult = .success(jobDTO("succeeded", inserted: 7))
        let vm = makeVM(fake)
        await vm.load()
        vm.startImport()
        await vm.awaitImportCompletion()
        XCTAssertEqual(vm.phase, .linked(handle: "magnus", gameCount: 7))
    }

    func testUnlinkReloadsToNotLinked() async {
        let fake = FakeLichessClient()
        fake.statusResults = [.success(statusDTO(linked: true, count: 3)), .success(statusDTO(linked: false))]
        let vm = makeVM(fake)
        await vm.load()
        await vm.unlink()
        XCTAssertEqual(vm.phase, .notLinked)
    }
}
