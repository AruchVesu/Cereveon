import XCTest
import UIKit
import CoreText
@testable import Cereveon

/// Verifies the Atrium brand fonts are actually bundled in the app and resolve
/// by the PostScript names `AtriumTypography.postScriptName(...)` looks up — the
/// thing that silently breaks if a file is missing, mis-named, or omitted from
/// `Info.plist`'s `UIAppFonts` (the UI would just fall back to system faces).
///
/// Host-independent: it locates each face in the app bundle and registers it
/// directly. That register is idempotent — if the test runs hosted and
/// `UIAppFonts` already registered the face, the redundant call is harmless — so
/// the assertion holds whether or not the run has the app as a test host.
final class BundledFontsTests: XCTestCase {

    /// For every Atrium face the filename stem equals the PostScript name.
    private let faces = [
        "CormorantGaramond-Regular",
        "CormorantGaramond-Medium",
        "CormorantGaramond-Italic",
        "CormorantGaramond-MediumItalic",
        "JetBrainsMono-Regular",
        "JetBrainsMono-Medium",
        "Inter-Regular",
        "Inter-Medium",
    ]

    func testAllAtriumFacesBundleAndResolveByPostScriptName() {
        // `NativeEngineProvider` is an app-module class, so this is the app
        // bundle (Cereveon.app) regardless of test-host configuration.
        let appBundle = Bundle(for: NativeEngineProvider.self)

        for face in faces {
            guard let url = appBundle.url(forResource: face, withExtension: "ttf") else {
                XCTFail("brand font not bundled: \(face).ttf")
                continue
            }
            _ = CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
            XCTAssertNotNil(
                UIFont(name: face, size: 12),
                "bundled but unresolvable by PostScript name '\(face)' — check the font's name table"
            )
        }
    }

    /// The lookup the typography layer actually uses must agree with the bundled
    /// PostScript names, so a rename on either side is caught.
    func testTypographyLookupMatchesBundledNames() {
        XCTAssertEqual(AtriumFontFamily.cormorant.postScriptName(weight: .regular, italic: false), "CormorantGaramond-Regular")
        XCTAssertEqual(AtriumFontFamily.cormorant.postScriptName(weight: .medium, italic: true), "CormorantGaramond-MediumItalic")
        XCTAssertEqual(AtriumFontFamily.jetBrains.postScriptName(weight: .medium, italic: false), "JetBrainsMono-Medium")
        XCTAssertEqual(AtriumFontFamily.inter.postScriptName(weight: .regular, italic: false), "Inter-Regular")
    }
}
