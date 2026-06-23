import SwiftUI

/// A standalone, board-less coach session opened from Lessons' "Start session".
/// Builds a fresh chat (fen = "startpos", no game), seeds the opening user turn
/// with a topic prompt, and reuses `ChatConversationView`. Mirrors Android's
/// `TrainingSessionBottomSheet` → `ChatBottomSheet(seedPrompt:)`.
struct LessonChatView: View {
    private let seed: String
    @StateObject private var chat: ChatViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var seeded = false

    init(topic: String, exerciseType: String, difficulty: String, auth: AuthViewModel) {
        seed = LessonChatSeed.prompt(topic: topic, exerciseType: exerciseType, difficulty: difficulty)
        _chat = StateObject(wrappedValue: ChatViewModel(
            client: HTTPChatClient(delegate: PinningURLSessionDelegate()),
            fen: { "startpos" },          // no live board — a topic discussion
            token: { auth.bearerToken }
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AtriumBackground()
                ChatConversationView(viewModel: chat,
                                     emptyTitle: "Starting your session…",
                                     emptySubtitle: "")
            }
            .navigationTitle("Coach")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    CoachVoiceMenu(viewModel: chat)
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }.foregroundStyle(AtriumColors.accentCyan)
                }
            }
            .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .tint(AtriumColors.accentCyan)
        .task {
            guard !seeded else { return }
            seeded = true
            await chat.preloadHistory()
            chat.draft = seed
            chat.send()      // auto-send the opening training turn
        }
    }
}

/// The opening-turn prompt for a topic session (pure; mirrors Android's
/// `TrainingSessionBottomSheet.buildSeedPrompt`).
enum LessonChatSeed {
    static func prompt(topic: String, exerciseType: String, difficulty: String) -> String {
        let topicText = topic.replacingOccurrences(of: "_", with: " ")
        let type = exerciseType.isEmpty
            ? ""
            : exerciseType.prefix(1).uppercased() + exerciseType.dropFirst()
        let diff = difficulty.lowercased()
        return "I want to train on \(topicText) (\(type), \(diff) difficulty). "
            + "Please guide me through this training session."
    }
}
