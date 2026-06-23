import XCTest
@testable import Cereveon

final class HomeHeaderTests: XCTestCase {

    func testInitials() {
        let dash = "\u{2014}"
        XCTAssertEqual(HomeHeader.initials(nil), dash)
        XCTAssertEqual(HomeHeader.initials(""), dash)
        XCTAssertEqual(HomeHeader.initials("   "), dash)
        XCTAssertEqual(HomeHeader.initials("demo"), dash, "demo identity → no initials")
        XCTAssertEqual(HomeHeader.initials("a1b2c3d4"), "A1")
        XCTAssertEqual(HomeHeader.initials("x"), "XX", "single char pads itself")
        XCTAssertEqual(HomeHeader.initials("--zz"), "ZZ", "non-alphanumerics skipped")
    }

    func testDateKicker() {
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        XCTAssertTrue(HomeHeader.dateKicker(now: now, firstSeen: now).contains("Day 001"))
        let fiveDaysAgo = now.addingTimeInterval(-5 * 86_400)
        XCTAssertTrue(HomeHeader.dateKicker(now: now, firstSeen: fiveDaysAgo).contains("Day 006"))
        XCTAssertTrue(HomeHeader.dateKicker(now: now, firstSeen: now).contains("·"))
    }

    func testXpKicker() {
        XCTAssertEqual(HomeHeader.xpKicker(xp: 0), "Level 1 · 0 XP")
        XCTAssertEqual(HomeHeader.xpKicker(xp: 100), "Level 2 · 100 XP")
        XCTAssertEqual(HomeHeader.xpKicker(xp: 250), "Level 3 · 250 XP")
        XCTAssertEqual(HomeHeader.xpKicker(xp: -10), "Level 1 · 0 XP")
    }
}

@MainActor
final class HomeHeaderViewModelTests: XCTestCase {

    private func freshDefaults() -> UserDefaults {
        UserDefaults(suiteName: "HomeHeaderTests-\(UUID().uuidString)")!
    }

    func testFirstSeenPersistsAcrossInstances() {
        let defaults = freshDefaults()
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let first = HomeHeaderViewModel(defaults: defaults, now: now)
        XCTAssertTrue(first.dateKicker(now: now).contains("Day 001"))

        // A later instance over the same store keeps the original first-seen.
        let threeDaysLater = now.addingTimeInterval(3 * 86_400)
        let later = HomeHeaderViewModel(defaults: defaults, now: threeDaysLater)
        XCTAssertTrue(later.dateKicker(now: threeDaysLater).contains("Day 004"))
    }

    func testLoadXP() async {
        let vm = HomeHeaderViewModel(defaults: freshDefaults())
        XCTAssertNil(vm.xpKicker)
        await vm.loadXP { 250 }
        XCTAssertEqual(vm.xpKicker, "Level 3 · 250 XP")
    }

    func testLoadXPNilProviderLeavesKickerNil() async {
        let vm = HomeHeaderViewModel(defaults: freshDefaults())
        await vm.loadXP { nil }
        XCTAssertNil(vm.xpKicker)
    }
}
