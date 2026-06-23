import SwiftUI

/// Cereveon · Atrium · Past games — the history list (iOS port of the Android
/// GameHistoryBottomSheet). Each row previews the outcome + last/winner move +
/// date; tapping pushes the passive replay. Presented full-screen from the Home
/// "Past games" library row.
struct GameHistoryView: View {
    @StateObject private var vm: GameHistoryViewModel
    @Environment(\.dismiss) private var dismiss
    private let token: () -> String?

    init(auth: AuthViewModel) {
        let provider: () -> String? = { auth.bearerToken }
        token = provider
        _vm = StateObject(wrappedValue: GameHistoryViewModel(
            client: HTTPGameHistoryClient(delegate: PinningURLSessionDelegate()),
            token: provider
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AtriumBackground()

                switch vm.state {
                case .loading:
                    ProgressView().tint(AtriumColors.accentCyan)
                case .error:
                    message("Couldn't load your games.", "Try again in a moment.")
                case .empty:
                    message("No games yet.", "Finish a game and it'll appear here.")
                case let .loaded(rows):
                    list(rows)
                }
            }
            .navigationTitle("Past games")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }.foregroundStyle(AtriumColors.accentCyan)
                }
            }
            .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .tint(AtriumColors.accentCyan)
        .task { await vm.load() }
    }

    private func list(_ rows: [GameHistoryViewModel.Row]) -> some View {
        ScrollView {
            VStack(spacing: 0) {
                ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                    NavigationLink {
                        GameReplayView(eventId: row.id, token: token)
                    } label: {
                        historyRow(row)
                    }
                    .buttonStyle(.plain)

                    if index < rows.count - 1 {
                        Rectangle().fill(AtriumColors.hairline).frame(height: AtriumSpacing.hairlineThickness)
                    }
                }
            }
            .padding(.horizontal, AtriumSpacing.space24)
            .padding(.vertical, AtriumSpacing.space12)
        }
    }

    private func historyRow(_ row: GameHistoryViewModel.Row) -> some View {
        HStack(spacing: AtriumSpacing.space12) {
            VStack(alignment: .leading, spacing: AtriumSpacing.space4) {
                Text(outcomeLabel(row.outcome))
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(outcomeColor(row.outcome))
                if !row.subtitle.isEmpty {
                    Text(row.subtitle)
                        .atriumStyle(AtriumTypography.inline)
                        .foregroundStyle(AtriumColors.muted)
                }
            }
            Spacer()
            if !row.date.isEmpty {
                Text(row.date.uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.dim)
            }
            Text("\u{203A}") // ›
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(AtriumColors.dim)
        }
        .padding(.vertical, AtriumSpacing.space12)
        .contentShape(Rectangle())
    }

    private func outcomeLabel(_ outcome: GameHistoryViewModel.Outcome) -> String {
        switch outcome {
        case .win: return "Win"
        case .loss: return "Loss"
        case .draw: return "Draw"
        case .other: return "Game"
        }
    }

    private func outcomeColor(_ outcome: GameHistoryViewModel.Outcome) -> Color {
        switch outcome {
        case .win: return AtriumColors.accentCyan
        case .loss: return AtriumColors.accentAmber
        case .draw: return AtriumColors.muted
        case .other: return AtriumColors.ink
        }
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
