import SwiftUI
import UIKit

/// Atrium typography — mirrors the Atrium.* text appearances in
/// android/app/src/main/res/values/themes.xml.
///
/// The custom fonts (Cormorant Garamond / JetBrains Mono / Inter) are bundled
/// under Resources/Fonts and registered via Info.plist `UIAppFonts`; `resolve`
/// still falls back to the closest system face if a face fails to load, so the
/// UI renders either way. `tracking` is in points (Android's em letterSpacing ×
/// point size).

enum AtriumFontWeight: Equatable {
    case regular  // 400
    case medium   // 500

    var swiftUI: Font.Weight { self == .medium ? .medium : .regular }
}

enum AtriumFontFamily {
    case cormorant   // serif — display / body
    case jetBrains   // monospaced — labels / numerics
    case inter       // sans — utility text

    func postScriptName(weight: AtriumFontWeight, italic: Bool) -> String {
        switch self {
        case .cormorant:
            switch (weight, italic) {
            case (.regular, false): return "CormorantGaramond-Regular"
            case (.regular, true):  return "CormorantGaramond-Italic"
            case (.medium,  false): return "CormorantGaramond-Medium"
            case (.medium,  true):  return "CormorantGaramond-MediumItalic"
            }
        case .jetBrains:
            return weight == .medium ? "JetBrainsMono-Medium" : "JetBrainsMono-Regular"
        case .inter:
            return weight == .medium ? "Inter-Medium" : "Inter-Regular"
        }
    }

    var systemDesign: Font.Design {
        switch self {
        case .cormorant: return .serif
        case .jetBrains: return .monospaced
        case .inter:     return .default
        }
    }
}

/// A resolved Atrium text style: a SwiftUI `Font` plus its tracking (pt).
struct AtriumTextStyle {
    let font: Font
    let tracking: CGFloat
}

enum AtriumTypography {
    //                              family       size  weight    italic  em-spacing
    static let display      = make(.cormorant,  28, .medium,  italic: true,  em: 0.011)
    static let displayLarge = make(.cormorant,  44, .medium,  italic: true,  em: 0.011)
    static let body         = make(.cormorant,  17, .regular, italic: false, em: 0.0)
    static let bodyItalic   = make(.cormorant,  17, .regular, italic: true,  em: 0.0)
    static let kicker       = make(.jetBrains,   9, .medium,  italic: false, em: 0.222)
    static let numeric      = make(.jetBrains,  11, .medium,  italic: false, em: 0.045)
    static let inline       = make(.inter,      13, .regular, italic: false, em: 0.0)
    // Kicker/Numeric "Cyan"/"Amber" variants share these metrics; the colour is
    // applied at the call site via AtriumColors, not baked into the font.

    private static func make(_ family: AtriumFontFamily,
                             _ size: CGFloat,
                             _ weight: AtriumFontWeight,
                             italic: Bool,
                             em: CGFloat) -> AtriumTextStyle {
        AtriumTextStyle(font: resolve(family, size: size, weight: weight, italic: italic),
                        tracking: em * size)
    }

    /// Custom font when installed, else the closest system face.
    private static func resolve(_ family: AtriumFontFamily,
                                size: CGFloat,
                                weight: AtriumFontWeight,
                                italic: Bool) -> Font {
        let name = family.postScriptName(weight: weight, italic: italic)
        if UIFont(name: name, size: size) != nil {
            return Font.custom(name, size: size)
        }
        var font = Font.system(size: size, weight: weight.swiftUI, design: family.systemDesign)
        if italic { font = font.italic() }
        return font
    }
}

extension View {
    /// Apply an Atrium text style (font + tracking) to a view.
    func atriumStyle(_ style: AtriumTextStyle) -> some View {
        self.font(style.font).tracking(style.tracking)
    }
}
