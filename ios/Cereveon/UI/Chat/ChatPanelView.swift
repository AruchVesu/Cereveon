import SwiftUI

/// The non-modal coach chat panel — a bottom-anchored, partial-height surface
/// over the live board. There is deliberately NO scrim: the board above the
/// panel stays tappable ("play while chatting"), mirroring Android's
/// `ChatBottomSheet` (a plain non-modal dialog with `FLAG_NOT_TOUCH_MODAL`, not a
/// scrim-backed bottom sheet). The reply streams into a growing bubble.
struct ChatPanelView: View {
    @ObservedObject var viewModel: ChatViewModel
    var onClose: () -> Void

    @FocusState private var composerFocused: Bool

    /// Scroll anchor pinned to the bottom of the list so the view follows the
    /// streaming text and new messages.
    private let bottomAnchor = "chat-bottom-anchor"

    var body: some View {
        VStack(spacing: 0) {
            grabber
            headerBar
            divider
            messageList
            divider
            composer
        }
        .background(AtriumColors.bgSurface)
        .overlay(alignment: .top) {
            Rectangle()
                .fill(AtriumColors.hairlineStrong)
                .frame(height: AtriumSpacing.hairlineThickness)
        }
        .task { await viewModel.preloadHistory() }
    }

    // MARK: - Chrome

    private var grabber: some View {
        Capsule()
            .fill(AtriumColors.hairlineStrong)
            .frame(width: 36, height: 4)
            .frame(maxWidth: .infinity)
            .padding(.top, AtriumSpacing.space8)
            .padding(.bottom, AtriumSpacing.space4)
    }

    private var headerBar: some View {
        HStack(spacing: AtriumSpacing.space8) {
            Text("Coach".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.accentCyan)

            if viewModel.isStreaming {
                Text("Thinking…".uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.muted)
            }

            Spacer()

            voiceMenu

            Button(action: onClose) {
                Text("\u{2715}") // ✕
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.muted)
                    .frame(width: AtriumSpacing.tapTarget, height: AtriumSpacing.space32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, AtriumSpacing.space16)
        .padding(.vertical, AtriumSpacing.space8)
    }

    /// Coach-tone picker. A `Menu` hosting a `Picker` renders the three voices as
    /// a checkmark radio set; the selection is the persisted `coachVoice`.
    private var voiceMenu: some View {
        Menu {
            Picker("Coach voice", selection: $viewModel.coachVoice) {
                ForEach(CoachVoice.allCases) { voice in
                    Text(voice.label).tag(voice)
                }
            }
        } label: {
            Text("Voice · \(viewModel.coachVoice.label)".uppercased())
                .atriumStyle(AtriumTypography.kicker)
                .foregroundStyle(AtriumColors.muted)
                .contentShape(Rectangle())
        }
        .tint(AtriumColors.accentCyan)
    }

    private var divider: some View {
        Rectangle()
            .fill(AtriumColors.hairline)
            .frame(height: AtriumSpacing.hairlineThickness)
    }

    // MARK: - Messages

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: AtriumSpacing.space12) {
                    if viewModel.messages.isEmpty {
                        emptyState
                    }
                    ForEach(viewModel.messages) { message in
                        ChatBubble(message: message).id(message.id)
                    }
                    Color.clear.frame(height: 1).id(bottomAnchor)
                }
                .padding(AtriumSpacing.space16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .onChange(of: viewModel.messages.count) { _ in
                withAnimation(.easeOut(duration: 0.18)) { proxy.scrollTo(bottomAnchor, anchor: .bottom) }
            }
            .onChange(of: viewModel.messages.last?.text) { _ in
                proxy.scrollTo(bottomAnchor, anchor: .bottom)
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            Text("Ask about this position.")
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
            Text("Plans, threats, what to study — the coach sees your live board.")
                .atriumStyle(AtriumTypography.inline)
                .foregroundStyle(AtriumColors.dim)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, AtriumSpacing.space24)
    }

    // MARK: - Composer

    private var composer: some View {
        HStack(alignment: .bottom, spacing: AtriumSpacing.space8) {
            ZStack(alignment: .leading) {
                if viewModel.draft.isEmpty {
                    Text("Ask the coach…")
                        .atriumStyle(AtriumTypography.body)
                        .foregroundStyle(AtriumColors.dim)
                        .padding(.horizontal, AtriumSpacing.space12)
                        .allowsHitTesting(false)
                }
                TextField("", text: $viewModel.draft, axis: .vertical)
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.ink)
                    .tint(AtriumColors.accentCyan)
                    .lineLimit(1...4)
                    .focused($composerFocused)
                    .padding(.horizontal, AtriumSpacing.space12)
            }
            .frame(minHeight: AtriumSpacing.tapTarget)
            .background(AtriumColors.bgBase)
            .overlay(
                RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                    .stroke(composerFocused ? AtriumColors.accentCyan : AtriumColors.hairlineStrong,
                            lineWidth: AtriumSpacing.hairlineThickness)
            )
            .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))

            Button { viewModel.send() } label: {
                Text("Send".uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(viewModel.canSend ? AtriumColors.accentCyan : AtriumColors.dim)
                    .frame(height: AtriumSpacing.tapTarget)
                    .padding(.horizontal, AtriumSpacing.space12)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!viewModel.canSend)
        }
        .padding(.horizontal, AtriumSpacing.space16)
        .padding(.vertical, AtriumSpacing.space12)
    }
}

/// One chat bubble. The coach speaks in serif italic (its established voice from
/// the dock); the player types in upright serif. User right + accent-tinted,
/// assistant left on the base surface.
private struct ChatBubble: View {
    let message: ChatViewModel.Message

    private var isUser: Bool { message.role == .user }

    var body: some View {
        HStack(spacing: 0) {
            if isUser { Spacer(minLength: AtriumSpacing.space32) }

            Text(message.text.isEmpty ? "\u{2026}" : message.text) // … while a stream is pending
                .atriumStyle(isUser ? AtriumTypography.body : AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.ink)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(isUser ? .trailing : .leading)
                .padding(.horizontal, AtriumSpacing.space12)
                .padding(.vertical, AtriumSpacing.space8)
                .background(isUser ? AtriumColors.accentCyan22 : AtriumColors.bgBase)
                .overlay(
                    RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius)
                        .stroke(isUser ? AtriumColors.accentCyan55 : AtriumColors.hairline,
                                lineWidth: AtriumSpacing.hairlineThickness)
                )
                .clipShape(RoundedRectangle(cornerRadius: AtriumSpacing.cornerRadius))

            if !isUser { Spacer(minLength: AtriumSpacing.space32) }
        }
        .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
    }
}
