import SwiftUI

/// On-board opening drill: reproduce the book line move-by-move. The board is
/// interactive while playing; each move is validated against the expected SAN.
/// Presented (in a NavigationStack) from `OpeningsView`.
struct OpeningDrillView: View {
    @StateObject private var vm: OpeningDrillViewModel
    @Environment(\.dismiss) private var dismiss

    init(opening: RepertoireOpening, onComplete: @escaping (Double) -> Void) {
        _vm = StateObject(wrappedValue: OpeningDrillViewModel(opening: opening, onComplete: onComplete))
    }

    var body: some View {
        ZStack {
            AtriumBackground()

            VStack(spacing: AtriumSpacing.space16) {
                ChessBoardView(
                    board: vm.board,
                    whiteToMove: vm.whiteToMove,
                    lastMoveFrom: vm.lastFrom,
                    lastMoveTo: vm.lastTo,
                    focusSquare: nil,
                    boardStyle: SettingsStore.boardStyle(),
                    isInteractive: vm.state == .playing,
                    onMove: { from, to in vm.attempt(from: from, to: to) }
                )
                .padding(.horizontal, AtriumSpacing.space16)

                statusArea
                Spacer(minLength: 0)
            }
            .padding(.top, AtriumSpacing.space12)
        }
        .navigationTitle("Drill · \(vm.opening.eco)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
    }

    @ViewBuilder
    private var statusArea: some View {
        switch vm.state {
        case .playing:
            VStack(spacing: AtriumSpacing.space8) {
                Text(vm.opening.name)
                    .atriumStyle(AtriumTypography.bodyItalic)
                    .foregroundStyle(AtriumColors.muted)
                Text("Move \(vm.ply + 1) of \(vm.totalPlies) · \(vm.whiteToMove ? "White" : "Black") to play")
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.dim)
                if let feedback = vm.feedback {
                    Text(feedback)
                        .atriumStyle(AtriumTypography.inline)
                        .foregroundStyle(AtriumColors.accentAmber)
                }
                AtriumSecondaryButton(title: "Show me the move") { vm.reveal() }
                    .padding(.horizontal, AtriumSpacing.space24)
            }

        case let .finished(_, mistakes):
            VStack(spacing: AtriumSpacing.space12) {
                Text(mistakes == 0 ? "Nailed it." : "\(mistakes) slip\(mistakes == 1 ? "" : "s").")
                    .atriumStyle(AtriumTypography.display)
                    .foregroundStyle(AtriumColors.ink)
                Text("Mastery updated.")
                    .atriumStyle(AtriumTypography.bodyItalic)
                    .foregroundStyle(AtriumColors.muted)
                AtriumPrimaryButton(title: "Done") { dismiss() }
                    .padding(.horizontal, AtriumSpacing.space24)
            }

        case .invalid:
            VStack(spacing: AtriumSpacing.space12) {
                Text("This line can't be drilled.")
                    .atriumStyle(AtriumTypography.bodyItalic)
                    .foregroundStyle(AtriumColors.muted)
                Text("It uses notation the drill can't parse yet.")
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.dim)
                    .multilineTextAlignment(.center)
                AtriumPrimaryButton(title: "Done") { dismiss() }
                    .padding(.horizontal, AtriumSpacing.space24)
            }
        }
    }
}
