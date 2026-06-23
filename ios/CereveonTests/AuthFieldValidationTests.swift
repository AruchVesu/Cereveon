import XCTest
@testable import Cereveon

/// Covers the auth-screen field rules extracted from `LoginView`. The password
/// floor mirrors `/auth/register`'s HTTP 400 below 8 characters, so the client
/// gates on it instead of surfacing a vague server error.
final class AuthFieldValidationTests: XCTestCase {

    func testPasswordAcceptedAtAndAboveMinimum() {
        XCTAssertEqual(AuthFieldValidation.minPasswordLength, 8)
        XCTAssertTrue(AuthFieldValidation.isAcceptablePassword("12345678"))   // exactly the floor
        XCTAssertTrue(AuthFieldValidation.isAcceptablePassword("a longer passphrase"))
    }

    func testPasswordRejectedBelowMinimum() {
        XCTAssertFalse(AuthFieldValidation.isAcceptablePassword(""))
        XCTAssertFalse(AuthFieldValidation.isAcceptablePassword("1234567"))   // one short
    }

    func testValidEmails() {
        for email in ["a@b.co", "user@example.com", "  spaced@domain.io  ", "x@sub.domain.org"] {
            XCTAssertTrue(AuthFieldValidation.isValidEmail(email), "should accept \(email)")
        }
    }

    func testInvalidEmails() {
        for email in ["", "noatsign.com", "@nodomain.com", "no@dotdomain",
                      "trailing@dot.", "leading@.dot", "  @x.com"] {
            XCTAssertFalse(AuthFieldValidation.isValidEmail(email), "should reject \(email)")
        }
    }
}
