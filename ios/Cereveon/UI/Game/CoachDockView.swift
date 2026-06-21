import SwiftUI

/// Quality of the human's last move, from the backend `move_quality` string.
enum MoveQuality: Equatable {
    case good, inaccuracy, mistake, blunder

    /// Mirrors Android `QuickCoachLogic.fromBackendString`; "unknown"/"" → nil
    /// (no badge shown).
    init?(backend: String) {
        switch backend.uppercased() {
        case "BLUNDER": self = .blunder
        case "MISTAKE": self = .mistake
        case "INACCURACY": self = .inaccuracy
        case "GOOD", "BEST", "OK": self = .good
        default: return nil
        }
    }

    var label: String {
        switch self {
        case .good: return "Solid"
        case .inaccuracy: return "Inaccuracy"
        case .mistake: return "Mistake"
        case .blunder: return "Blunder"
        }
    }

    var color: Color {
        switch self {
        case .good: return AtriumColors.accentCyan
        case .inaccuracy, .mistake, .blunder: return AtriumColors.accentAmber
        }
    }
}

/// The coach dock: the `/live/move` hint plus a move-quality badge. Text only —
/// no numeric evaluation (that is the eval band's job).
struct CoachDockView: View {
    let hint: String?
    let quality: MoveQuality?

    var body: some View {
        if hint == nil && quality == nil {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
                HStack(spacing: AtriumSpacing.space8) {
                    Text("Coach".uppercased())
                        .atriumStyle(AtriumTypography.kicker)
                        .foregroundStyle(AtriumColors.accentCyan)
                    if let quality {
                        Text(quality.label.uppercased())
                            .atriumStyle(AtriumTypography.kicker)
                            .foregroundStyle(quality.color)
                    }
                }
                if let hint {
                    Text(hint)
                        .atriumStyle(AtriumTypography.bodyItalic)
                        .foregroundStyle(AtriumColors.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(AtriumSpacing.cardPadding)
            .background(AtriumColors.bgSurface)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(AtriumColors.hairline, lineWidth: AtriumSpacing.hairlineThickness)
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
        }
    }
}
