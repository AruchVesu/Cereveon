import SwiftUI

/// Coarse evaluation band. The five steps from White's perspective.
enum EvalBand: Int, Equatable {
    case losing = 0, worse, equal, better, winning

    /// Map a centipawn score (White's POV; ±10000 = mate) to a coarse band.
    static func from(centipawns cp: Int?) -> EvalBand {
        guard let cp else { return .equal }
        switch cp {
        case ..<(-250):       return .losing
        case (-250)..<(-60):  return .worse
        case (-60)...60:      return .equal
        case 61...250:        return .better
        default:              return .winning
        }
    }
}

enum EvalSide { case white, black }

/// Atrium eval band — the ONLY permitted visual representation of the engine
/// evaluation (no numbers, no PV lines, no move arrows). Five steps along a
/// track with a glowing dot, ported from Android `EvalBandView`. The leading
/// gradient fills to the dot in the signal colour (cyan = White ahead,
/// amber = Black ahead) only when one side is clearly better; neutral otherwise.
struct EvalBandView: View {
    var band: EvalBand = .equal
    var side: EvalSide = .white

    private var position: CGFloat { CGFloat(band.rawValue) / 4 }

    private var signalColor: Color {
        (band == .winning || band == .better)
            ? (side == .white ? AtriumColors.accentCyan : AtriumColors.accentAmber)
            : AtriumColors.muted
    }

    var body: some View {
        Canvas { context, size in
            let w = size.width, h = size.height, cy = h / 2
            let trackH: CGFloat = 6, dotR: CGFloat = 6

            // Track background.
            context.fill(
                Path(roundedRect: CGRect(x: 0, y: cy - trackH / 2, width: w, height: trackH), cornerRadius: trackH / 2),
                with: .color(AtriumColors.hairline)
            )
            // Five band ticks.
            for i in 0...4 {
                let tx = CGFloat(i) / 4 * w
                context.fill(
                    Path(CGRect(x: tx - 0.5, y: cy - trackH / 2, width: 1, height: trackH)),
                    with: .color(AtriumColors.hairlineStrong)
                )
            }
            // Filled portion, transparent → signal.
            let fillEnd = max(0, min(position * w, w))
            if fillEnd > 0 {
                context.fill(
                    Path(roundedRect: CGRect(x: 0, y: cy - trackH / 2, width: fillEnd, height: trackH), cornerRadius: trackH / 2),
                    with: .linearGradient(Gradient(colors: [.clear, signalColor]),
                                          startPoint: .zero, endPoint: CGPoint(x: fillEnd, y: 0))
                )
            }
            // Indicator dot with a soft halo.
            let dotX = min(max(position * w, dotR), w - dotR)
            let dotRect = CGRect(x: dotX - dotR, y: cy - dotR, width: dotR * 2, height: dotR * 2)
            context.drawLayer { layer in
                layer.addFilter(.blur(radius: dotR))
                layer.fill(Path(ellipseIn: dotRect), with: .color(signalColor))
            }
            context.fill(Path(ellipseIn: dotRect), with: .color(signalColor))
        }
        .frame(height: 16)
        .accessibilityLabel("Evaluation: \(accessibilityBand)")
    }

    private var accessibilityBand: String {
        switch band {
        case .losing: return "losing"
        case .worse: return "worse"
        case .equal: return "equal"
        case .better: return "better"
        case .winning: return "winning"
        }
    }
}
