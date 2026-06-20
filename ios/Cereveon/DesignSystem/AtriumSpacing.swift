import CoreGraphics

/// Atrium spacing scale (dp → pt, 1:1). Mirrors
/// android/app/src/main/res/values/dimens.xml.
enum AtriumSpacing {
    static let space4:  CGFloat = 4
    static let space8:  CGFloat = 8
    static let space12: CGFloat = 12
    static let space16: CGFloat = 16
    static let space20: CGFloat = 20
    static let space24: CGFloat = 24
    static let space32: CGFloat = 32
    static let space44: CGFloat = 44

    static let tapTarget: CGFloat = 44
    static let cornerRadius: CGFloat = 2
    static let hairlineThickness: CGFloat = 1
    static let cardPadding: CGFloat = 16
    static let textPaddingHorizontal: CGFloat = 24
}
