import SwiftUI

/// The play screen: the human (White) versus the on-device engine (Black), with
/// the live coaching layer — the eval band above the board and the hint dock
/// below it. Built with `init(auth:)` so the coaching clients carry the player's
/// Bearer token (and TLS pinning).
struct PlayView: View {
    @StateObject private var vm: PlayViewModel
    @StateObject private var chat: ChatViewModel
    @Environment(\.dismiss) private var dismiss

    /// Non-modal coach panel visibility. When open, the board above the panel
    /// stays tappable (no scrim) — "play while chatting".
    @State private var showChat = false
    @State private var containerHeight: CGFloat = 0

    init(auth: AuthViewModel) {
        let pinning = PinningURLSessionDelegate()
        let play = PlayViewModel(
            liveCoach: HTTPLiveMoveClient(delegate: pinning),
            evalClient: HTTPEngineEvalClient(delegate: pinning),
            gameClient: HTTPGameClient(delegate: pinning),
            token: { auth.bearerToken }
        )
        _vm = StateObject(wrappedValue: play)
        // The chat reads the live board / game / last-move from the same view
        // model so every turn carries the position the user currently sees.
        _chat = StateObject(wrappedValue: ChatViewModel(
            client: HTTPChatClient(delegate: pinning),
            fen: { play.currentFEN },
            gameId: { play.activeGameId },
            lastMove: { play.lastMoveUci },
            moveCount: { play.halfMoveCount },
            token: { auth.bearerToken }
        ))
    }

    /// Bottom panel height — roughly half the screen so the upper board stays
    /// visible and tappable while chatting.
    private var panelHeight: CGFloat {
        guard containerHeight > 0 else { return 360 }
        return max(300, containerHeight * 0.52)
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            AtriumBackground()

            VStack(spacing: AtriumSpacing.space16) {
                header
                EvalBandView(band: vm.evalBand)
                    .padding(.horizontal, AtriumSpacing.space24)
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
                CoachDockView(hint: vm.coachHint, quality: vm.moveQuality)
                    .padding(.horizontal, AtriumSpacing.space16)
                Spacer(minLength: 0)
            }
            .padding(.top, AtriumSpacing.space12)

            // Coach affordance — hidden while the panel is open. The play loop
            // behind the panel stays live, so this only opens the conversation.
            if !showChat {
                coachButton
                    .padding(.trailing, AtriumSpacing.space24)
                    .padding(.bottom, AtriumSpacing.space24)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
            }

            // Non-modal chat panel: no scrim, so the board above stays tappable.
            if showChat {
                ChatPanelView(viewModel: chat, onClose: { showChat = false })
                    .frame(height: panelHeight)
                    .transition(.move(edge: .bottom))
            }

            // Modal overlays sit ABOVE the chat panel (a pending promotion or a
            // finished game takes precedence over the conversation).
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
        .background(
            GeometryReader { geo in
                Color.clear
                    .onAppear { containerHeight = geo.size.height }
                    .onChange(of: geo.size.height) { containerHeight = $0 }
            }
        )
        .animation(.easeInOut(duration: 0.22), value: showChat)
    }

    private var coachButton: some View {
        Button { showChat = true } label: {
            Text("Coach".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)
                .padding(.horizontal, AtriumSpacing.space16)
                .frame(height: AtriumSpacing.tapTarget)
                .background(AtriumColors.bgSurface)
                .overlay(
                    RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                        .stroke(AtriumColors.accentCyan55, lineWidth: AtriumSpacing.hairlineThickness)
                )
                .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
        }
        .buttonStyle(.plain)
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
