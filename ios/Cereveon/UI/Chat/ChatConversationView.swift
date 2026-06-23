import SwiftUI

/// The reusable coach-chat conversation: the auto-scrolling message list (with
/// 👍/👎 on settled assistant replies) + the composer. Hosted both by the
/// over-board `ChatPanelView` and the standalone `LessonChatView`; the chrome
/// (grabber / header / nav bar) is the host's concern.
struct ChatConversationView: View {
    @ObservedObject var viewModel: ChatViewModel
    /// Empty-state copy — the panel mentions the live board; a standalone session
    /// uses neutral copy.
    var emptyTitle: String = "Ask the coach."
    var emptySubtitle: String = "Plans, threats, what to study."

    @FocusState private var composerFocused: Bool
    private let bottomAnchor = "chat-bottom-anchor"

    var body: some View {
        VStack(spacing: 0) {
            messageList
            divider
            composer
        }
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
                        ChatBubble(message: message, onFeedback: feedbackHandler(for: message))
                            .id(message.id)
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

    /// Feedback closure for a bubble — nil (no thumbs) for user messages and for
    /// the assistant bubble that is still streaming.
    private func feedbackHandler(for message: ChatViewModel.Message) -> ((Bool) -> Void)? {
        let isStreamingThis = viewModel.isStreaming && message.id == viewModel.messages.last?.id
        guard message.role == .assistant, !isStreamingThis else { return nil }
        return { helpful in viewModel.submitFeedback(for: message, helpful: helpful) }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            Text(emptyTitle)
                .atriumStyle(AtriumTypography.bodyItalic)
                .foregroundStyle(AtriumColors.muted)
            if !emptySubtitle.isEmpty {
                Text(emptySubtitle)
                    .atriumStyle(AtriumTypography.inline)
                    .foregroundStyle(AtriumColors.dim)
                    .fixedSize(horizontal: false, vertical: true)
            }
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

/// One chat bubble. The coach speaks in serif italic; the player types upright.
/// User right + accent-tinted, assistant left on the base surface.
private struct ChatBubble: View {
    let message: ChatViewModel.Message
    var onFeedback: ((Bool) -> Void)? = nil

    private var isUser: Bool { message.role == .user }

    var body: some View {
        HStack(spacing: 0) {
            if isUser { Spacer(minLength: AtriumSpacing.space32) }

            VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
                bubbleText
                if let onFeedback, !message.text.isEmpty {
                    feedbackRow(onFeedback)
                }
            }

            if !isUser { Spacer(minLength: AtriumSpacing.space32) }
        }
        .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
    }

    private var bubbleText: some View {
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
    }

    private func feedbackRow(_ onFeedback: @escaping (Bool) -> Void) -> some View {
        HStack(spacing: AtriumSpacing.space16) {
            thumb(up: true, selected: message.feedback == true) { onFeedback(true) }
            thumb(up: false, selected: message.feedback == false) { onFeedback(false) }
        }
        .padding(.leading, AtriumSpacing.space4)
    }

    private func thumb(up: Bool, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: up ? "hand.thumbsup" : "hand.thumbsdown")
                .font(.system(size: 12))
                .foregroundStyle(selected ? AtriumColors.accentCyan : AtriumColors.dim)
                .frame(width: AtriumSpacing.space24, height: AtriumSpacing.space20)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
