import XCTest
import UIKit
import CoreText
@testable import Cereveon

/// Verifies the Atrium brand fonts are actually bundled in the app, resolve by
/// the PostScript names `AtriumTypography.postScriptName(...)` looks up, and —
/// after Latin-subsetting — still carry the glyphs the UI renders. These are the
/// things that silently break (a missing/mis-named file, a dropped `UIAppFonts`
/// entry, or an over-aggressive subset) and degrade to system faces unnoticed.
///
/// Host-independent: `setUp` registers each face from the app bundle directly
/// (idempotent if a hosted run's `UIAppFonts` already did), so the assertions
/// hold whether or not the run has the app as a test host.
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

    /// `NativeEngineProvider` is an app-module class, so this is the app bundle
    /// (Cereveon.app) regardless of test-host configuration.
    private var appBundle: Bundle { Bundle(for: NativeEngineProvider.self) }

    override func setUp() {
        super.setUp()
        for face in faces {
            if let url = appBundle.url(forResource: face, withExtension: "ttf") {
                CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
            }
        }
    }

    func testAllAtriumFacesBundleAndResolveByPostScriptName() {
        for face in faces {
            XCTAssertNotNil(appBundle.url(forResource: face, withExtension: "ttf"),
                            "brand font not bundled: \(face).ttf")
            XCTAssertNotNil(UIFont(name: face, size: 12),
                            "bundled but unresolvable by PostScript name '\(face)' — check the name table")
        }
    }

    /// Each face must resolve to *itself* (not a system substitute) and still
    /// cover the full printable-ASCII range — the floor for any UI text, and the
    /// tripwire for a subset that drops glyphs the typography needs.
    func testSubsetFacesResolveAndCoverPrintableAscii() {
        let ascii: [UniChar] = (0x20...0x7E).map { UniChar($0) }
        for face in faces {
            let ctFont = CTFontCreateWithName(face as CFString, 12, nil)
            XCTAssertEqual(CTFontCopyPostScriptName(ctFont) as String, face,
                           "\(face) did not resolve to the bundled face (registration/subset issue)")
            var glyphs = [CGGlyph](repeating: 0, count: ascii.count)
            XCTAssertTrue(CTFontGetGlyphsForCharacters(ctFont, ascii, &glyphs, ascii.count),
                          "\(face): missing printable-ASCII glyphs after subsetting")
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
