import XCTest
@testable import Cereveon

final class GameSnapshotTests: XCTestCase {

    private func freshDefaults() -> UserDefaults {
        UserDefaults(suiteName: "GameSnapshotTests-\(UUID().uuidString)")!
    }

    private func snapshot(moveCount: Int = 2, savedAt: Date = Date(), gameNumber: Int = 5) -> GameSnapshot {
        GameSnapshot(uciHistory: ["e2e4", "e7e5"], fen: "x",
                     moveCount: moveCount, gameNumber: gameNumber, savedAt: savedAt)
    }

    func testSaveLoadClearRoundTrip() {
        let defaults = freshDefaults()
        XCTAssertNil(GameSnapshotStore.load(defaults: defaults))
        let snap = snapshot()
        GameSnapshotStore.save(snap, defaults: defaults)
        XCTAssertEqual(GameSnapshotStore.load(defaults: defaults), snap)
        GameSnapshotStore.clear(defaults: defaults)
        XCTAssertNil(GameSnapshotStore.load(defaults: defaults))
    }

    func testResumableWithinTTL() {
        let defaults = freshDefaults()
        GameSnapshotStore.save(snapshot(savedAt: Date()), defaults: defaults)
        XCTAssertNotNil(GameSnapshotStore.resumable(defaults: defaults))
    }

    func testResumableNilWhenStale() {
        let defaults = freshDefaults()
        let now = Date()
        GameSnapshotStore.save(snapshot(savedAt: now.addingTimeInterval(-7 * 3600)), defaults: defaults)
        XCTAssertNil(GameSnapshotStore.resumable(now: now, defaults: defaults), "7h old → stale")
    }

    func testResumableNilWhenNoMoves() {
        let defaults = freshDefaults()
        GameSnapshotStore.save(snapshot(moveCount: 0), defaults: defaults)
        XCTAssertNil(GameSnapshotStore.resumable(defaults: defaults))
    }

    func testNextGameNumberIncrements() {
        let defaults = freshDefaults()
        XCTAssertEqual(GameSnapshotStore.nextGameNumber(defaults: defaults), 1)
        XCTAssertEqual(GameSnapshotStore.nextGameNumber(defaults: defaults), 2)
        XCTAssertEqual(GameSnapshotStore.nextGameNumber(defaults: defaults), 3)
    }

    func testResumeTitle() {
        XCTAssertEqual(snapshot(moveCount: 14, gameNumber: 47).resumeTitle, "Game 047 · move 14")
        XCTAssertEqual(snapshot(moveCount: 0, gameNumber: 0).resumeTitle, "Game 001 · move 0")
    }
}
