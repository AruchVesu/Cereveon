import SwiftUI

/// Phase-1 landing placeholder. The real Home (board + coach chat) arrives in
/// Phase 2; this confirms the auth/onboarding flow reached its terminal state
/// and offers a "Log out" affordance that returns to LoginView via the view
/// model. Atrium-styled to match the rest of the flow.
struct HomeStubView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @State private var showPlay = false

    private var isWorking: Bool {
        if case .working = auth.phase { return true }
        return false
    }

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)

            VStack(spacing: AtriumSpacing.space16) {
                Text("Cereveon".uppercased())
                    .atriumStyle(AtriumTypography.kicker)
                    .foregroundStyle(AtriumColors.accentCyan)

                Text("You're in.")
                    .atriumStyle(AtriumTypography.displayLarge)
                    .foregroundStyle(AtriumColors.ink)

                AtriumOrnamentRule()
                    .padding(.horizontal, AtriumSpacing.space32)

                Text("Play a game — the coach arrives next.")
                    .atriumStyle(AtriumTypography.bodyItalic)
                    .foregroundStyle(AtriumColors.muted)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            VStack(spacing: AtriumSpacing.space12) {
                AtriumPrimaryButton(title: "New game") { showPlay = true }
                AtriumSecondaryButton(title: "Log out") {
                    Task { await auth.logout() }
                }
                .disabled(isWorking)
            }
            .padding(.bottom, AtriumSpacing.space32)
        }
        .padding(.horizontal, AtriumSpacing.textPaddingHorizontal)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .fullScreenCover(isPresented: $showPlay) {
            PlayView(auth: auth)
        }
    }
}
