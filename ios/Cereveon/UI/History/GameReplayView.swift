import SwiftUI

/// Passive replay of a finished game — a non-interactive board stepped through
/// the per-ply positions with transport controls. Pushed from `GameHistoryView`.
/// (Coaching-during-replay, as on Android's main-board review, is a follow-up.)
struct GameReplayView: View {
    @StateObject private var vm: GameReplayViewModel
    private let eventId: String
    private let token: () -> String?

    init(eventId: String, token: @escaping () -> String?) {
        self.eventId = eventId
        self.token = token
        _vm = StateObject(wrappedValue: GameReplayViewModel(
            eventId: eventId,
            client: HTTPGameHistoryClient(delegate: PinningURLSessionDelegate()),
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
            case .ready:
                replay
            }
        }
        .navigationTitle("Replay")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await vm.load() }
    }

    private var replay: some View {
        VStack(spacing: AtriumSpacing.space16) {
            ChessBoardView(
                board: vm.board,
                whiteToMove: vm.whiteToMove,
                lastMoveFrom: nil,
                lastMoveTo: nil,
                focusSquare: nil,
                boardStyle: SettingsStore.boardStyle(),
                isInteractive: false,
                onMove: { _, _ in }
            )
            .padding(.horizontal, AtriumSpacing.space16)

            controls
            sharpenLink
            Spacer(minLength: 0)
        }
        .padding(.top, AtriumSpacing.space12)
    }

    /// Launches the "sharpen" trainer on this game's positions.
    private var sharpenLink: some View {
        NavigationLink {
            MistakeReplayView(eventId: eventId, token: token)
        } label: {
            Text("Test yourself".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
                .frame(maxWidth: .infinity)
                .frame(height: AtriumSpacing.tapTarget)
                .overlay(
                    RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                        .stroke(AtriumColors.accentCyan55, lineWidth: AtriumSpacing.hairlineThickness)
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, AtriumSpacing.space24)
        .padding(.top, AtriumSpacing.space8)
    }

    private var controls: some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text(vm.moveLabel)
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.ink)
            Text("\(vm.index) / \(vm.plyCount)")
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.muted)

            HStack(spacing: AtriumSpacing.space24) {
                stepButton("\u{23EE}", enabled: vm.canBack) { vm.goToStart() }   // ⏮
                stepButton("\u{25C0}", enabled: vm.canBack) { vm.stepBack() }     // ◀
                stepButton("\u{25B6}", enabled: vm.canForward) { vm.stepForward() } // ▶
                stepButton("\u{23ED}", enabled: vm.canForward) { vm.goToEnd() }   // ⏭
            }
            .padding(.top, AtriumSpacing.space4)
        }
        .padding(.horizontal, AtriumSpacing.space16)
    }

    private func stepButton(_ glyph: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(glyph)
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(enabled ? AtriumColors.accentCyan : AtriumColors.dim)
                .frame(width: AtriumSpacing.space44, height: AtriumSpacing.space44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }

    private func message(_ title: String, _ subtitle: String) -> some View {
        VStack(spacing: AtriumSpacing.space8) {
            Text(title)
                .atriumStyle(AtriumTypography.display)
                .foregroundStyle(AtriumColors.ink)
            Text(subtitle)
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
                .multilineTextAlignment(.center)
        }
        .padding(AtriumSpacing.space32)
    }
}
