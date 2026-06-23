import SwiftUI

/// Cereveon · Atrium · Settings — the "You" tab destination (iOS port of the
/// Android `SettingsBottomSheet`). Coach-voice + board-style radios, sound +
/// notification toggles, and an account section (change password, sign out).
/// Presented as a sheet from `HomeView`; hosts a `NavigationStack` so change
/// password can push.
struct SettingsView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @StateObject private var store = SettingsStore()
    @Environment(\.dismiss) private var dismiss

    @State private var confirmSignOut = false

    private var isWorking: Bool {
        if case .working = auth.phase { return true }
        return false
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AtriumBackground()

                ScrollView {
                    VStack(alignment: .leading, spacing: AtriumSpacing.space24) {
                        profileSection
                        rule
                        voiceSection
                        rule
                        boardSection
                        rule
                        preferencesSection
                        rule
                        integrationsSection
                        rule
                        accountSection
                    }
                    .padding(AtriumSpacing.space24)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(AtriumColors.accentCyan)
                }
            }
            .toolbarBackground(AtriumColors.bgBase, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .tint(AtriumColors.accentCyan)
        .confirmationDialog("Sign out of Cereveon?", isPresented: $confirmSignOut, titleVisibility: .visible) {
            Button("Sign out", role: .destructive) {
                dismiss()
                Task { await auth.logout() }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    // MARK: - Sections

    private var profileSection: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            sectionHeader("Profile")
            NavigationLink {
                ProgressDashboardView(token: { auth.bearerToken })
            } label: {
                chevronRowLabel("Your progress")
            }
            .buttonStyle(.plain)
        }
    }

    private var voiceSection: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            sectionHeader("Coach voice")
            ForEach(CoachVoice.allCases) { voice in
                radioRow(voice.label, selected: store.coachVoice == voice) {
                    store.coachVoice = voice
                }
            }
        }
    }

    private var boardSection: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            sectionHeader("Board style")
            ForEach(BoardStyle.allCases, id: \.self) { style in
                radioRow(style.label, selected: store.boardStyle == style) {
                    store.boardStyle = style
                }
            }
        }
    }

    private var preferencesSection: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            sectionHeader("Preferences")
            toggleRow("Sound", isOn: $store.soundEnabled)
            toggleRow("Notifications", isOn: $store.notificationsEnabled)
        }
    }

    private var integrationsSection: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            sectionHeader("Integrations")
            NavigationLink {
                LichessConnectView(token: { auth.bearerToken })
            } label: {
                chevronRowLabel("Lichess")
            }
            .buttonStyle(.plain)
        }
    }

    private var accountSection: some View {
        VStack(alignment: .leading, spacing: AtriumSpacing.space8) {
            sectionHeader("Account")
            NavigationLink {
                ChangePasswordView()
            } label: {
                chevronRowLabel("Change password")
            }
            .buttonStyle(.plain)

            Button {
                guard !isWorking else { return }
                confirmSignOut = true
            } label: {
                HStack {
                    Text("Sign out")
                        .atriumStyle(AtriumTypography.body)
                        .foregroundStyle(AtriumColors.accentAmber)
                    Spacer()
                }
                .padding(.vertical, AtriumSpacing.space12)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Components

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .atriumStyle(AtriumTypography.kicker)
            .foregroundStyle(AtriumColors.muted)
    }

    private var rule: some View {
        Rectangle()
            .fill(AtriumColors.hairline)
            .frame(height: AtriumSpacing.hairlineThickness)
    }

    private func radioRow(_ label: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(label)
                    .atriumStyle(AtriumTypography.body)
                    .foregroundStyle(AtriumColors.ink)
                Spacer()
                Circle()
                    .fill(selected ? AtriumColors.accentCyan : Color.clear)
                    .frame(width: 10, height: 10)
                    .overlay(
                        Circle().stroke(selected ? AtriumColors.accentCyan : AtriumColors.hairlineStrong,
                                        lineWidth: AtriumSpacing.hairlineThickness)
                    )
            }
            .padding(.vertical, AtriumSpacing.space12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func toggleRow(_ label: String, isOn: Binding<Bool>) -> some View {
        Toggle(isOn: isOn) {
            Text(label)
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(AtriumColors.ink)
        }
        .tint(AtriumColors.accentCyan)
        .padding(.vertical, AtriumSpacing.space4)
    }

    private func chevronRowLabel(_ label: String) -> some View {
        HStack {
            Text(label)
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(AtriumColors.ink)
            Spacer()
            Text("\u{203A}") // ›
                .atriumStyle(AtriumTypography.body)
                .foregroundStyle(AtriumColors.dim)
        }
        .padding(.vertical, AtriumSpacing.space12)
        .contentShape(Rectangle())
    }
}
