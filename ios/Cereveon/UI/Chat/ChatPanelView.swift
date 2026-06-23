import SwiftUI

/// The non-modal coach chat panel — a bottom-anchored, partial-height surface
/// over the live board. There is deliberately NO scrim: the board above the
/// panel stays tappable ("play while chatting"), mirroring Android's
/// `ChatBottomSheet` (a plain non-modal dialog with `FLAG_NOT_TOUCH_MODAL`, not a
/// scrim-backed bottom sheet). The conversation itself is `ChatConversationView`;
/// this view adds the grabber + header chrome.
struct ChatPanelView: View {
    @ObservedObject var viewModel: ChatViewModel
    /// Panel height, owned by the host (PlayView) so the board layout behind it
    /// stays consistent; the grabber drags it within `heightBounds`.
    @Binding var height: CGFloat
    let heightBounds: ClosedRange<CGFloat>
    var onClose: () -> Void

    /// Panel height when the resize drag began (nil = not dragging).
    @State private var dragStartHeight: CGFloat? = nil

    var body: some View {
        VStack(spacing: 0) {
            grabber
            headerBar
            Rectangle()
                .fill(AtriumColors.hairline)
                .frame(height: AtriumSpacing.hairlineThickness)
            ChatConversationView(
                viewModel: viewModel,
                emptyTitle: "Ask about this position.",
                emptySubtitle: "Plans, threats, what to study — the coach sees your live board."
            )
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

    /// Drag handle. Dragging it up grows the panel, down shrinks it — bounded by
    /// `heightBounds` so the board above always keeps a tappable strip.
    private var grabber: some View {
        Capsule()
            .fill(AtriumColors.hairlineStrong)
            .frame(width: 36, height: 4)
            .frame(maxWidth: .infinity)
            .padding(.top, AtriumSpacing.space8)
            .padding(.bottom, AtriumSpacing.space4)
            .contentShape(Rectangle())
            .gesture(resizeGesture)
    }

    private var resizeGesture: some Gesture {
        DragGesture(minimumDistance: 2)
            .onChanged { value in
                let start = dragStartHeight ?? height
                if dragStartHeight == nil { dragStartHeight = start }
                let proposed = start - value.translation.height // up = taller
                height = min(max(proposed, heightBounds.lowerBound), heightBounds.upperBound)
            }
            .onEnded { _ in dragStartHeight = nil }
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

            CoachVoiceMenu(viewModel: viewModel)

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
}

/// Coach-tone picker. A `Menu` hosting a `Picker` renders the three voices as a
/// checkmark radio set; the selection is the persisted `coachVoice`. Shared by
/// the chat panel header and the lesson-session toolbar.
struct CoachVoiceMenu: View {
    @ObservedObject var viewModel: ChatViewModel

    var body: some View {
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
}
