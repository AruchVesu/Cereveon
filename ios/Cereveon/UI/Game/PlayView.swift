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
    /// Resizable chat-panel height (0 until first opened); clamped to panelBounds.
    @State private var panelHeight: CGFloat = 0
    /// Board render chosen in Settings, read once when this screen is presented.
    @State private var boardStyle = SettingsStore.boardStyle()
    /// Post-game "Replay your mistake" cover.
    @State private var showMistakeReplay = false
    /// Bearer provider, kept for the mistake-replay cover.
    private let token: () -> String?

    init(auth: AuthViewModel, resume: GameSnapshot? = nil) {
        let pinning = PinningURLSessionDelegate()
        let play = PlayViewModel(
            liveCoach: HTTPLiveMoveClient(delegate: pinning),
            evalClient: HTTPEngineEvalClient(delegate: pinning),
            gameClient: HTTPGameClient(delegate: pinning),
            token: { auth.bearerToken },
            resume: resume
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
        token = { auth.bearerToken }
    }

    /// Resize bounds: never below ~⅓ or above ~⅞ of the screen, so the upper
    /// board always keeps a tappable strip ("play while chatting").
    private var panelBounds: ClosedRange<CGFloat> {
        let h = containerHeight > 0 ? containerHeight : 600
        return (h * 0.32)...(h * 0.88)
    }

    /// Opening height — ~55% of the screen.
    private var defaultPanelHeight: CGFloat {
        (containerHeight > 0 ? containerHeight : 600) * 0.55
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
                    boardStyle: boardStyle,
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
                ChatPanelView(viewModel: chat,
                              height: $panelHeight,
                              heightBounds: panelBounds,
                              onClose: { showChat = false })
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
        .onChange(of: containerHeight) { _ in
            guard panelHeight > 0 else { return }
            panelHeight = min(max(panelHeight, panelBounds.lowerBound), panelBounds.upperBound)
        }
        .fullScreenCover(isPresented: $showMistakeReplay) {
            if let fen = vm.gameSummary?.biggestMistake?.fen, !fen.isEmpty {
                NavigationStack {
                    MistakeReplayView(positions: [fen], token: token)
                        .toolbar {
                            ToolbarItem(placement: .navigationBarLeading) {
                                Button("Close") { showMistakeReplay = false }
                                    .foregroundStyle(AtriumColors.muted)
                            }
                        }
                }
                .tint(AtriumColors.accentCyan)
            }
        }
    }

    /// Open the panel: initialise the height on first open; otherwise keep the
    /// last size, re-clamped in case the screen rotated since.
    private func openChat() {
        panelHeight = panelHeight <= 0
            ? defaultPanelHeight
            : min(max(panelHeight, panelBounds.lowerBound), panelBounds.upperBound)
        showChat = true
    }

    private var coachButton: some View {
        Button { openChat() } label: {
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

            if let summary = vm.gameSummary, summaryHasContent(summary) {
                coachSummary(summary)
            }

            if let mistake = vm.gameSummary?.biggestMistake, mistake.isReplayable {
                AtriumPrimaryButton(title: "Replay your mistake") { showMistakeReplay = true }
                AtriumSecondaryButton(title: "New game") { vm.newGame() }
            } else {
                AtriumPrimaryButton(title: "New game") { vm.newGame() }
            }
            AtriumSecondaryButton(title: "Home") { dismiss() }
        }
        .padding(AtriumSpacing.space24)
        .frame(maxWidth: 340)
        .background(AtriumColors.bgSurface)
        .overlay(
            RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                .stroke(AtriumColors.hairlineStrong, lineWidth: AtriumSpacing.hairlineThickness)
        )
        .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))
    }

    private func summaryHasContent(_ summary: GameFinishResponse) -> Bool {
        summary.coachAction.hasContent
            || !summary.coachContent.title.isEmpty
            || !summary.coachContent.description.isEmpty
    }

    /// The coach's post-game plan (action badge + title + description), from
    /// `/game/finish`. Rating/confidence are deliberately not shown (Elo hidden).
    private func coachSummary(_ summary: GameFinishResponse) -> some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            if summary.coachAction.hasContent {
                Text(actionLabel(summary.coachAction.type).uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.accentAmber)
            }
            if !summary.coachContent.title.isEmpty {
                Text(summary.coachContent.title)
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !summary.coachContent.description.isEmpty {
                Text(summary.coachContent.description)
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func actionLabel(_ type: String) -> String {
        switch type.uppercased() {
        case "DRILL": return "Drill"
        case "PUZZLE": return "Puzzle"
        case "REFLECT": return "Reflect"
        case "PLAN_UPDATE": return "Plan update"
        case "CELEBRATE": return "Celebrate"
        default: return "Coach"
        }
    }

    private func resultHeadline(_ result: GameResult) -> String {
        switch result {
        case .whiteWins: return "You win."
        case .blackWins: return "Cereveon wins."
        case .draw: return "A drawn game."
        }
    }
}
