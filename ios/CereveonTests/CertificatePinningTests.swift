import XCTest
import Security
@testable import Cereveon

/// Validates the SPKI pin computation against the *published* ISRG root pins —
/// the same values configured in `AppConfig` and on Android. If the ASN.1 header
/// reconstruction were wrong, these would not match. The fixtures are the public
/// ISRG root certificates (X1 = RSA-4096, X2 = EC-P384), covering both header
/// paths. The full `allows(...)` flow depends on a real validated trust chain
/// (system-CA evaluation), so it is exercised on-device, matching Android's
/// instrumented NetworkSecurityCertPinningTest boundary.
final class CertificatePinningTests: XCTestCase {

    private func loadCert(_ name: String) throws -> SecCertificate {
        let url = try XCTUnwrap(
            Bundle(for: Self.self).url(forResource: name, withExtension: "pem"),
            "missing test resource \(name).pem"
        )
        let pem = try String(contentsOf: url, encoding: .utf8)
        let base64 = pem
            .replacingOccurrences(of: "-----BEGIN CERTIFICATE-----", with: "")
            .replacingOccurrences(of: "-----END CERTIFICATE-----", with: "")
            .components(separatedBy: .whitespacesAndNewlines)
            .joined()
        let der = try XCTUnwrap(Data(base64Encoded: base64), "bad base64 in \(name).pem")
        return try XCTUnwrap(SecCertificateCreateWithData(nil, der as CFData), "bad cert \(name).pem")
    }

    /// RSA-4096 header path: ISRG Root X1's computed SPKI pin must equal the
    /// published value pinned in AppConfig.
    func testSPKIMatchesISRGRootX1() throws {
        let pin = CertificatePinning.spkiSHA256Base64(for: try loadCert("isrgrootx1"))
        XCTAssertEqual(pin, "C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=")
    }

    /// EC-P384 header path: ISRG Root X2.
    func testSPKIMatchesISRGRootX2() throws {
        let pin = CertificatePinning.spkiSHA256Base64(for: try loadCert("isrg-root-x2"))
        XCTAssertEqual(pin, "diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI=")
    }

    /// Both computed pins are present in the configured pin set (guards against
    /// a typo'd AppConfig value).
    func testComputedPinsAreInConfiguredSet() throws {
        for name in ["isrgrootx1", "isrg-root-x2"] {
            let pin = try XCTUnwrap(CertificatePinning.spkiSHA256Base64(for: loadCert(name)))
            XCTAssertTrue(AppConfig.pinnedSPKISHA256.contains(pin), "\(name) pin not in AppConfig set")
        }
    }

    func testOnlyCereveonHostIsPinned() {
        XCTAssertTrue(CertificatePinning.isPinnedHost("cereveon.com"))
        XCTAssertTrue(CertificatePinning.isPinnedHost("api.cereveon.com"))
        XCTAssertTrue(CertificatePinning.isPinnedHost("CEREVEON.COM"))
        XCTAssertFalse(CertificatePinning.isPinnedHost("evil.com"))
        XCTAssertFalse(CertificatePinning.isPinnedHost("notcereveon.com"))
        XCTAssertFalse(CertificatePinning.isPinnedHost("cereveon.com.evil.com"))
    }

    func testExpiryDateMatchesAndroidPinSet() throws {
        let expiry = try XCTUnwrap(CertificatePinning.expiryDate())
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let expected = calendar.date(from: DateComponents(year: 2028, month: 5, day: 20))
        XCTAssertEqual(expiry, expected)
    }
}
