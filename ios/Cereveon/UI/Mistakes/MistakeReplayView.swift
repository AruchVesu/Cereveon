import SwiftUI

/// "Sharpen" — find the engine's best move in positions from one of your games,
/// judged by /training/verify-replay. Pushed from `GameReplayView`. (The standalone
/// on-board "solve" surface the curriculum can't provide.)
struct MistakeReplayView: View {
    @StateObject private var vm: MistakeReplayViewModel
    @Environment(\.dismiss) private var dismiss

    init(eventId: String, token: @escaping () -> String?) {
        _vm = StateObject(wrappedValue: MistakeReplayViewModel(
            eventId: eventId,
            historyClient: HTTPGameHistoryClient(delegate: PinningURLSessionDelegate()),
            verifyClient: HTTPVerifyReplayClient(delegate: PinningURLSessionDelegate()),
            token: token
        ))
    }

    /// Drill specific positions directly — used by the post-game "Replay your
    /// mistake" CTA with the single biggest-mistake FEN.
    init(positions: [String], token: @escaping () -> String?) {
        _vm = StateObject(wrappedValue: MistakeReplayViewModel(
            eventId: "",
            seedFENs: positions,
            historyClient: HTTPGameHistoryClient(delegate: PinningURLSessionDelegate()),
            verifyClient: HTTPVerifyReplayClient(delegate: PinningURLSessionDelegate()),
            token: token
        ))
    }

    var body: some View {
        ZStack {
            AtriumBackground()
            switch vm.state {
            case .loading:
                ProgressView().tint(AtriumColors.accentCyan)
            case .error:
                message("Couldn't load this game.", "Try again in a moment.")
            case .empty:
                message("Nothing to sharpen here.", "This game is too short to drill.")
            case .ready:
                puzzle
            case let .finished(correct, total):
                finished(correct, total)
            }
        }
        .navigationTitle("Sharpen")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await vm.load() }
    }

    private var puzzle: some View {
        VStack(spacing: AtriumSpacing.space16) {
            ChessBoardView(
                board: vm.board,
                whiteToMove: vm.whiteToMove,
                lastMoveFrom: vm.bestFrom,
                lastMoveTo: vm.bestTo,
                focusSquare: nil,
                boardStyle: SettingsStore.boardStyle(),
                isInteractive: vm.isInteractive,
                onMove: { from, to in vm.attempt(from: from, to: to) }
            )
            .padding(.horizontal, AtriumSpacing.space16)

            statusArea
            Spacer(minLength: 0)
        }
        .padding(.top, AtriumSpacing.space12)
    }

    private var statusArea: some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text("Position \(vm.index + 1) of \(vm.total) · White to play")
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.dim)

            if let feedback = vm.feedback {
                Text(feedback)
                    .atriumStyle(AtriumTypography.bodyItalic)
                    .foregroundStyle(feedback.contains("\u{2713}") ? AtriumColors.accentCyan : AtriumColors.accentAmber)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Find the engine's best move.")
                    .atriumStyle(AtriumTypography.bodyItalic)
                    .foregroundStyle(AtriumColors.muted)
            }

            if vm.verifying {
                ProgressView().tint(AtriumColors.accentCyan)
            }
            if vm.solved {
                AtriumPrimaryButton(title: vm.index + 1 >= vm.total ? "Finish" : "Next") { vm.next() }
                    .padding(.horizontal, AtriumSpacing.space24)
            }
        }
        .padding(.horizontal, AtriumSpacing.space16)
    }

    private func finished(_ correct: Int, _ total: Int) -> some View {
        VStack(spacing: AtriumSpacing.space12) {
            Text("\(correct) of \(total)")
                .atriumStyle(AtriumTypography.displayLarge)
                .foregroundStyle(AtriumColors.ink)
            Text(correct == total ? "Perfect — every best move found." : "Best moves found.")
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
            AtriumPrimaryButton(title: "Done") { dismiss() }
                .padding(.horizontal, AtriumSpacing.space24)
        }
        .padding(AtriumSpacing.space32)
    }

    private func message(_ title: String, _ subtitle: String) -> some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text(title).atriumStyle(AtriumTypography.display).foregroundStyle(AtriumColors.ink)
            Text(subtitle).atriumStyle(AtriumTypography.bodyItalic).foregroundStyle(AtriumColors.muted)
                .multilineTextAlignment(.center)
        }
        .padding(AtriumSpacing.space32)
    }
}
