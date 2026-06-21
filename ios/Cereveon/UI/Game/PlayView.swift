import SwiftUI

/// The play screen: the human (White) versus the on-device engine (Black).
/// Hosts `ChessBoardView`, the promotion picker, and a game-over overlay, all
/// driven by `PlayViewModel`. Phase 2c-i is local play only — `/live/move`
/// coaching, the eval band, and game persistence are layered on in 2c-ii.
struct PlayView: View {
    @StateObject private var vm = PlayViewModel()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            AtriumBackground()

            VStack(spacing: AtriumSpacing.space20) {
                header
                ChessBoardView(
                    board: vm.board,
                    whiteToMove: vm.whiteToMove,
                    lastMoveFrom: vm.lastMoveFrom,
                    lastMoveTo: vm.lastMoveTo,
                    focusSquare: nil,
                    boardStyle: .flat,
                    isInteractive: vm.isHumanTurn,
                    onMove: { from, to in vm.onMove(from: from, to: to) }
                )
                .padding(.horizontal, AtriumSpacing.space16)
                statusLine
                Spacer(minLength: 0)
            }
            .padding(.top, AtriumSpacing.space12)

            if vm.pendingPromotion != nil {
                modalOverlay {
                    // The human always plays White in this phase.
                    PromotionPickerView(isWhite: true) { kind in vm.completePromotion(kind) }
                }
            }

            if let result = vm.gameResult {
                modalOverlay { gameOverCard(result) }
            }
        }
    }

    // MARK: - Chrome

    private var header: some View {
        HStack {
            Button { dismiss() } label: {
                Text("‹ Home")
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.muted)
            }
            Spacer()
            Text("Cereveon".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
            Spacer()
            Button { vm.newGame() } label: {
                Text("New game")
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.accentCyan)
            }
        }
        .padding(.horizontal, AtriumSpacing.textPaddingHorizontal)
    }

    private var statusLine: some View {
        Text(statusText)
            .atriumStyle(AtriumTypography.bodyItalic)
            .foregroundStyle(AtriumColors.muted)
            .frame(height: AtriumSpacing.space24)
    }

    private var statusText: String {
        if vm.gameResult != nil { return "" }
        if vm.aiThinking { return "Cereveon is thinking…" }
        return vm.whiteToMove ? "Your move." : ""
    }

    // MARK: - Overlays

    private func modalOverlay<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        ZStack {
            Color.black.opacity(0.55).ignoresSafeArea()
            content()
                .padding(AtriumSpacing.space24)
        }
    }

    private func gameOverCard(_ result: GameResult) -> some View {
        VStack(spacing: AtriumSpacing.space16) {
            Text(result == .draw ? "Draw" : "Checkmate")
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
            Text(resultHeadline(result))
                .atriumStyle(AtriumTypography.display)
                .foregroundStyle(AtriumColors.ink)
                .multilineTextAlignment(.center)
            AtriumOrnamentRule()
            AtriumPrimaryButton(title: "New game") { vm.newGame() }
            AtriumSecondaryButton(title: "Home") { dismiss() }
        }
        .padding(AtriumSpacing.space24)
        .frame(maxWidth: 320)
        .background(AtriumColors.bgSurface)
        .overlay(
            RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                .stroke(AtriumColors.hairlineStrong, lineWidth: AtriumSpacing.hairlineThickness)
        )
        .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
    }

    private func resultHeadline(_ result: GameResult) -> String {
        switch result {
        case .whiteWins: return "You win."
        case .blackWins: return "Cereveon wins."
        case .draw: return "A drawn game."
        }
    }
}
