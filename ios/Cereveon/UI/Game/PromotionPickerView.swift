import SwiftUI

/// Atrium-styled pawn-promotion picker: a row of four piece tiles (Queen, Rook,
/// Bishop, Knight). Tapping one calls `onSelect` with the lowercase kind
/// character ("q" / "r" / "b" / "n"); the parent applies it via
/// `ChessGame.promote(at:to:)`.
///
/// Pure presentation — it holds no game state. The glyphs are tinted for the
/// promoting side exactly as the board renders pieces: White = ivory glyph +
/// soft cyan halo, Black = warm-obsidian glyph + amber rim.
struct PromotionPickerView: View {
    let isWhite: Bool
    let onSelect: (Character) -> Void   // "q" / "r" / "b" / "n" (lowercase)

    // Queen, Rook, Bishop, Knight — the legal promotion targets, in the
    // conventional order. Glyphs use the same solid Unicode set as the board.
    private static let choices: [(kind: Character, glyph: String, label: String)] = [
        ("q", "\u{265B}", "Queen"),   // ♛
        ("r", "\u{265C}", "Rook"),    // ♜
        ("b", "\u{265D}", "Bishop"),  // ♝
        ("n", "\u{265E}", "Knight"),  // ♞
    ]

    var body: some View {
        VStack(spacing: AtriumSpacing.space16) {
            Text("Promote".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)

            HStack(spacing: AtriumSpacing.space12) {
                ForEach(Self.choices, id: \.kind) { choice in
                    PromotionTile(
                        glyph: choice.glyph,
                        label: choice.label,
                        isWhite: isWhite,
                        action: { onSelect(choice.kind) }
                    )
                }
            }
        }
        .padding(AtriumSpacing.cardPadding)
        .background(AtriumColors.bgSurface)
        .overlay(
            RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                .stroke(AtriumColors.hairlineStrong, lineWidth: AtriumSpacing.hairlineThickness)
        )
        .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
    }
}

/// A single promotion tile: the piece glyph with its Atrium halo over a
/// hairline-bordered surface, plus a mono caption.
private struct PromotionTile: View {
    let glyph: String
    let label: String
    let isWhite: Bool
    let action: () -> Void

    private var glyphColor: Color { isWhite ? AtriumColors.pieceWhite : AtriumColors.pieceBlack }
    private var haloColor: Color { isWhite ? AtriumColors.accentCyan : AtriumColors.accentAmber }

    var body: some View {
        Button(action: action) {
            VStack(spacing: AtriumSpacing.space8) {
                Text(glyph)
                    .font(.system(size: 40))
                    .foregroundStyle(glyphColor)
                    // Soft halo behind the glyph, mirroring the board's piece glow.
                    .shadow(color: haloColor.opacity(0.85), radius: isWhite ? 8 : 6)
                Text(label.uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.dim)
            }
            .frame(width: AtriumSpacing.space44 + AtriumSpacing.space16,
                   height: AtriumSpacing.space44 + AtriumSpacing.space20)
            .background(AtriumColors.bgBase)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(AtriumColors.accentCyan55, lineWidth: AtriumSpacing.hairlineThickness)
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
