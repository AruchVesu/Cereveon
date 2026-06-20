import SwiftUI

/// Cereveon · Atrium colour tokens — dark-only. Values mirror
/// android/app/src/main/res/values/colors.xml exactly; this is the source of
/// truth for the iOS app's palette.
enum AtriumColors {
    // Surfaces
    static let bgBase        = Color(hex: 0x0A0A10)
    static let bgSurface     = Color(hex: 0x0D0E14)
    static let bgGradientTop = Color(hex: 0x16141F)

    // Ink
    static let ink   = Color(hex: 0xF4EFE1)
    static let muted = Color(hex: 0x9AA0B4)
    static let dim   = Color(hex: 0x6B7080)

    // Hairlines / dividers (white at low alpha)
    static let hairline       = Color(hex: 0xFFFFFF, alpha: 0x14 / 255.0)  // ~8%
    static let hairlineStrong = Color(hex: 0xFFFFFF, alpha: 0x1F / 255.0)  // ~12%

    // Accents
    static let accentCyan  = Color(hex: 0x4FD9E5)  // player, signal, focus
    static let accentAmber = Color(hex: 0xFFC069)  // opponent, warnings, black-piece rim

    // Translucent accent variants (alpha = the AA in Android's #AARRGGBB)
    static let accentCyan55  = Color(hex: 0x4FD9E5, alpha: 0x55 / 255.0)
    static let accentCyan22  = Color(hex: 0x4FD9E5, alpha: 0x22 / 255.0)
    static let accentCyan2e  = Color(hex: 0x4FD9E5, alpha: 0x2E / 255.0)  // last-move "to"
    static let accentCyan1a  = Color(hex: 0x4FD9E5, alpha: 0x1A / 255.0)  // last-move "from"
    static let accentAmberCc = Color(hex: 0xFFC069, alpha: 0xCC / 255.0)  // black piece rim
    static let accentAmber55 = Color(hex: 0xFFC069, alpha: 0x55 / 255.0)  // black piece halo

    // Board palette
    static let boardLight = Color(hex: 0x302C24)  // warm wood
    static let boardDark  = Color(hex: 0x1A1712)
    static let pieceWhite = Color(hex: 0xF4EFE1)
    static let pieceBlack = Color(hex: 0x1A1108)

    // Legacy aliases (mirror colors.xml back-compat names)
    static let bg     = bgBase
    static let neon   = accentCyan
    static let select = accentAmber
}

extension Color {
    /// Build from a 0xRRGGBB hex with optional alpha in 0...1.
    init(hex: UInt32, alpha: Double = 1.0) {
        let r = Double((hex >> 16) & 0xFF) / 255.0
        let g = Double((hex >> 8) & 0xFF) / 255.0
        let b = Double(hex & 0xFF) / 255.0
        self.init(.sRGB, red: r, green: g, blue: b, opacity: alpha)
    }
}
